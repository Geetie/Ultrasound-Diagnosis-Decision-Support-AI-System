import { useCallback, useEffect, useRef, useState } from 'react';
import {
  confirmDiagnosisResult,
  pollDiagnosisResult,
  requestDiagnosis,
  requestMultiOrganDiagnosis,
  DEFAULT_RESPONSE_TIER,
} from '@/services/diagnosisService';
import { getApiErrorMessage } from '@/utils/apiError';
import type {
  CrossOrganBlock,
  DiagnosisResult,
  MultiOrganDiagnosisResponse,
  OrganDiagnosisStatus,
} from '@/types/api';

/**
 * 检查工作区的判读接线 —— 17-§5 裁定 4 的落地。
 *
 * `/diagnosis` 独立页删除后，`DiagnosisPage` 的三项能力迁到这里：
 *   · LoadingPhase（含 timeout / error）→ 本hook 的 phase
 *   · useProgressiveDiagnosis（结论先落地）→ pollDiagnosisResult
 *   · reviewAcknowledged（「已人工核验」）→ confirm()
 *
 * ⛔ 进度**只用 2 个词**（17-§3.1）：「分析中」→「已完成」。
 * `diagnosisService.STAGE_LABELS` 里那 6 个内部阶段名（检索知识库 / 融合校验 /
 * 安全网关…）是工程语汇，**医生不关心**，本 hook 只把「阶段」用于埋点与超基线
 * 提示，**绝不允许渲染成界面文字** —— 那正是 v3 被削减掉的东西，不许复活。
 */

export type FlowPhase = 'idle' | 'scanning' | 'analyzing' | 'concluded' | 'timeout' | 'error';

export interface WorkspaceFlow {
  phase: FlowPhase;
  /** 超P99 基线时才补一句真话（契约§3.1 允许的唯一额外文案）。 */
  overtimeNote: string | null;
  result: DiagnosisResult | null;
  error: string | null;
  /**
   * 本次判读的 request_id —— **报告与签名页的入口靠它**。
   *
   * 为什么必须由flow 暴露：`/ui/report` 是靠 URL 上的 `request_id` 取报告的
   * （`ReportPage` 只认这个参数），而 request_id 是发起判读时**后端**给的。
   * 不把它交出去，医生在界面上就只能手敲 URL 才能写报告 —— 实测确实如此。
   */
  requestId: string | null;
  /** 生成进度（A.43）：processing 期间已生成字符数；completed 后归零。 */
  progressChars: number;
  /** 安全预览（A.46）：已过段落级 SG 的文本尾部；SG 拒绝/无数据 = null。 */
  previewText: string | null;
  /**
   * 多器官联合诊断的汇总结果（会话 `target_organs` ≥ 2 时才有）。
   *
   * null = 本次不是多器官流程（或还没跑完）。**不能**把 null 当成"多部位没问题"——
   * 它只说明"没走多器官端点"。`crossOrgan` 的三态才是诚实的口径。
   */
  multiOrgan: MultiOrganDiagnosisResponse | null;
  /** 跨器官融合块。空对象/缺失 = 未执行；`status` 三态见 `CrossOrganBlock`。 */
  crossOrgan: CrossOrganBlock | null;
  /** 各器官执行结果（含失败器官的可读 `reason`）。空数组 = 未跑多器官。 */
  organResults: OrganDiagnosisStatus[];
  /**
   * 发起**多器官**联合判读（会话含 ≥2 个目标部位时走这条）。
   *
   * 为什么必须存在：`POST /diagnosis/multi-organ` 是跨器官冲突检测与跨器官一致性
   * 唯一能拿到 ≥2 份独立结论的真实入口，此前前端零调用 → 两条防线生产上从未被喂过。
   */
  startMultiOrgan: (input: {
    sessionId: string;
    organs: string[];
    tier?: 'realtime' | 'standard' | 'full';
  }) => Promise<void>;
  /**
   * 单独重跑某一个器官（多器官流程里失败/跳过的那些）。
   *
   * 实现方式：仍打多器官端点，但 `organs` 只传这一个 —— 后端按传入列表串行处理，
   * 无需另开单器官端点（单器官端点不会回填 `organ_statuses`，状态会前后不一致）。
   */
  rerunOrgan: (organ: string) => Promise<void>;
  /** 发起判读（冻结后自动触发，不需要医生点按钮）。 */
  start: (input: {
    sessionId: string;
    imageIds: string[];
    targetOrgan: string;
    tier?: 'realtime' | 'standard' | 'full';
    /** A.36 ⑤：临床信息进判读上下文（后端 DiagnosisRequest 现成字段）。 */
    clinicalText?: string;
    patientContext?: {
      age?: number;
      gender?: 'M' | 'F';
      /**
       * 安全网关禁忌条件，**规范码**（pregnancy / contrast_allergy /
       * anticoagulant_therapy）。码表唯一来源：`clinicalInfo.CONDITION_CODES`。
       *
       * ⚠️ `[]` 与 `undefined` 语义相反（后端 `PatientContext.conditions` 约定）：
       * `[]` = 已确认无条件 → SG 正常评估；`undefined` = 未提供 → SG 保守判命中
       * → 恒需人工复核。调用方必须送 `[]`，见 `toConditionCodes()`。
       */
      conditions?: string[];
    };
  }) => Promise<void>;
  /** 「确认结论」三选一等效里的点按钮那条（说「确认」/ 脚踏走各自的通道）。 */
  confirm: () => void;
  /** 「已人工核验」勾选，对应 reviewAcknowledged。 */
  acknowledged: boolean;
  setAcknowledged: (v: boolean) => void;
  /** 确认的提交状态：确认是异步回写，失败要能让医生看见。 */
  confirming: boolean;
  confirmError: string | null;
  /** 服务端记录的确诊时间（ISO 串）；null = 尚未确认。 */
  confirmedAt: string | null;
  /**
   * 接管**已有** request_id：只轮询取结果，不再新发一次判读。
   *
   * 为什么需要：语音控件（AppLayout）在拿到 request_id 后跳 `/?request_id=…`，
   * 那个请求已经发出并落库了 —— 若此时走 start() 会重复提交一次判读。
   * 幂等键留空由后端去重也救不了：这里根本不该再发请求。
   */
  resume: (requestId: string, tier?: 'realtime' | 'standard' | 'full') => void;
}

const P99_MS = 4800; // 14-文档 P99 基线；超时才提示，不常驻倒计时

/**
 * 按器官合并多器官结果：**新结果覆盖同器官旧结果**，其余保留原顺序。
 *
 * 为什么需要：单独重跑一个器官时，后端只返回被重跑部位的那一条；
 * 直接替换整个数组会把其它部位的结果抹掉（界面会显示成"其它部位没跑"）。
 */
function mergeOrganResults(
  prev: OrganDiagnosisStatus[],
  next: OrganDiagnosisStatus[]
): OrganDiagnosisStatus[] {
  const byOrgan = new Map(prev.map((r) => [r.organ, r]));
  for (const r of next) byOrgan.set(r.organ, r);
  // 保持 prev 的顺序（= 会话 target_organs 顺序），新出现的器官追加在后
  const order = [
    ...prev.map((r) => r.organ),
    ...next.map((r) => r.organ).filter((o) => !prev.some((p) => p.organ === o)),
  ];
  return order.map((organ) => byOrgan.get(organ)!).filter(Boolean);
}

export function useWorkspaceFlow(): WorkspaceFlow {
  const [phase, setPhase] = useState<FlowPhase>('idle');
  const [overtimeNote, setOvertimeNote] = useState<string | null>(null);
  const [result, setResult] = useState<DiagnosisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  // 确认回写（2026-10-28 P0-4）：此前 confirm() 只 setAcknowledged(true)，
  // 全仓无人读、后端无端点 → 医生点了没有任何痕迹。现在真发请求、真落库。
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [confirmedAt, setConfirmedAt] = useState<string | null>(null);
  // 生成进度（A.43）：后端流式写 Redis → 轮询带回 →「分析中」显示已生成字数。
  // 结论出后归零（下一轮判读重新累积）。
  const [progressChars, setProgressChars] = useState<number>(0);
  // 安全预览（A.46）：已过段落级 SG 的生成文本尾部；SG 拒绝 → null（临床红线：
  // 未过 SG 的原始输出不上屏）。结论出后归零。
  const [previewText, setPreviewText] = useState<string | null>(null);
  // 多器官（T-GB-AGT-01）：汇总结果 / 跨器官块 / 各器官状态。null = 未走多器官。
  const [multiOrgan, setMultiOrgan] = useState<MultiOrganDiagnosisResponse | null>(null);
  const [crossOrgan, setCrossOrgan] = useState<CrossOrganBlock | null>(null);
  const [organResults, setOrganResults] = useState<OrganDiagnosisStatus[]>([]);

  const startedAt = useRef<number>(0);
  const cancelPoll = useRef<{ cancel: () => void } | null>(null);

  useEffect(() => () => cancelPoll.current?.cancel(), []);

  const start = useCallback<WorkspaceFlow['start']>(async (input) => {
    setPhase('analyzing');
    setError(null);
    setResult(null);
    setAcknowledged(false);
    setOvertimeNote(null);
    setProgressChars(0);
    setPreviewText(null);
    startedAt.current = Date.now();

    let newRequestId: string;
    try {
      const resp = await requestDiagnosis({
        session_id: input.sessionId,
        image_ids: input.imageIds,
        target_organ: input.targetOrgan,
        response_tier: input.tier ?? DEFAULT_RESPONSE_TIER,
        clinical_text: input.clinicalText || undefined,
        patient_context: input.patientContext,
        // 幂等键：留空由后端按会话去重，避免前端每次生成随机 key 造成永不命中
      } as never);
      newRequestId = (resp as { request_id?: string }).request_id ?? '';
    } catch (e) {
      setError((e as Error)?.message || '判读请求失败');
      setPhase('error');
      return;
    }

    if (!newRequestId) {
      setError('后端未返回 request_id');
      setPhase('error');
      return;
    }
    // 交给上层做「报告与签名」入口 —— 不暴露它，医生就只能手敲 URL
    setRequestId(newRequestId);

    cancelPoll.current?.cancel();
    cancelPoll.current = pollDiagnosisResult(
      newRequestId,
      (r) => {
        setResult(r);
        setPhase('concluded');
        // 超基线才补一句真话（这是契约允许的唯一额外文案）
        const spent = (Date.now() - startedAt.current) / 1000;
        if (spent * 1000 > P99_MS) {
          setOvertimeNote(`本次耗时 ${spent.toFixed(1)}s，已超基线 4.8s`);
        }
      },
      () => setPhase('timeout'),
      {
        tier: input.tier ?? DEFAULT_RESPONSE_TIER,
        // 阶段事件只用于埋点/超基线判断，不进界面（⛔ 不许把 6 个阶段名渲染给医生）
        onStage: () => undefined,
        // 生成进度（A.43）：processing 中间态透传已生成字符数 → 「分析中」显示
        onProgress: setProgressChars,
        // 安全预览（A.46）：已过段落级 SG 的文本；拒绝 → 置空（临床红线）
        onPreview: setPreviewText,
        onPreviewRefused: () => setPreviewText(null),
      }
    );
  }, []);

  const confirm = useCallback(() => {
    if (phase !== 'concluded' || !requestId) return;
    // 幂等：已确认或正在提交时不重复发请求（防连点产生多条审计）
    if (confirming || confirmedAt) return;

    setConfirming(true);
    setConfirmError(null);
    void confirmDiagnosisResult(requestId)
      .then((res) => {
        setConfirmedAt(res.doctor_confirmed_at);
        setAcknowledged(true);
      })
      .catch((err: unknown) => {
        // 确认失败必须让医生看见：静默失败等于"以为确认了其实没有"，
        // 而这恰恰是 automation bias 的反面——医生以为系统记下了。
        setConfirmError(getApiErrorMessage(err, '确认失败，请重试'));
        setAcknowledged(false);
      })
      .finally(() => setConfirming(false));
  }, [phase, requestId, confirming, confirmedAt]);

  /**
   * 宽泛地接收后端的 `cross_organ`：可能是块，也可能是空对象（未执行）。
   * 空对象没有 `status` → 返回 null，**不要**合成一个 `ok`，那等于把"没跑"说成"没问题"。
   */
  const pickCrossOrgan = (raw: unknown): CrossOrganBlock | null => {
    if (!raw || typeof raw !== 'object') return null;
    const block = raw as CrossOrganBlock;
    return typeof block.status === 'string' && block.status !== '' ? block : null;
  };

  /**
   * 多器官联合判读（T-GB-AGT-01）。
   *
   * 与 `start()` 的差别：一次请求跑完会话全部目标器官，完成后**没有单个
   * request_id**（每个器官各一个）—— 所以这里不设 `requestId`，报告入口改由
   * 各器官的 `request_id` 提供（见 `organResults`）。
   *
   * ⚠️ 部分完成（`partial=true`）不是错误：后端明确「单器官失败不阻断其余器官」。
   * 这里如实落 `multiOrgan` / `organResults`，由界面区分"完成/失败/跳过"，
   * **绝不**把 partial 显示成全部完成。
   */
  const startMultiOrgan = useCallback<WorkspaceFlow['startMultiOrgan']>(async (input) => {
    setPhase('analyzing');
    setError(null);
    setResult(null);
    setMultiOrgan(null);
    setCrossOrgan(null);
    setOrganResults([]);
    setAcknowledged(false);
    setOvertimeNote(null);
    setProgressChars(0);
    setPreviewText(null);
    startedAt.current = Date.now();

    // 多器官不支持轮询中间进度（后端是同步串行返回），所以不接 pollDiagnosisResult。
    cancelPoll.current?.cancel();
    cancelPoll.current = null;

    let resp: MultiOrganDiagnosisResponse;
    try {
      resp = await requestMultiOrganDiagnosis({
        session_id: input.sessionId,
        organs: input.organs,
        response_tier: input.tier ?? DEFAULT_RESPONSE_TIER,
      });
    } catch (e) {
      // 零静默：技术细节进控制台，医生可见出口是 phase=error 的界面文案
      console.error('[workspace-flow] 多器官判读请求失败', e);
      setError((e as Error)?.message || '多部位判读请求失败');
      setPhase('error');
      return;
    }

    setMultiOrgan(resp);
    setOrganResults(resp.organ_results ?? []);
    setCrossOrgan(pickCrossOrgan(resp.cross_organ));
    setPhase('concluded');

    const spent = (Date.now() - startedAt.current) / 1000;
    if (spent * 1000 > P99_MS) {
      setOvertimeNote(`本次耗时 ${spent.toFixed(1)}s，已超基线 4.8s`);
    }
  }, []);

  /**
   * 单独重跑一个器官 —— 打多器官端点但只传这一个部位。
   *
   * 为什么复用多器官端点而不是 `/diagnosis/request`：只有前者会回写会话
   * `organ_statuses` 并回填 `organ_results`；用单器官端点重跑会让部位状态条
   * 与真实状态不一致（"已跑"和"没跑"同时存在）。
   * 多器官结果里该器官成功后就把它从失败/跳过名单自然剔除（整体重新拉取）。
   */
  const rerunOrgan = useCallback<WorkspaceFlow['rerunOrgan']>(
    async (organ) => {
      const sid = multiOrgan?.session_id;
      if (!sid) {
        // 没有会话上下文就重跑不了 —— 说出来，不静默失败
        console.error('[workspace-flow] 器官重跑缺少会话上下文', { organ });
        setError('无法单独重跑该部位：缺少本次检查的会话标识');
        return;
      }
      setPhase('analyzing');
      setError(null);
      startedAt.current = Date.now();
      let resp: MultiOrganDiagnosisResponse;
      try {
        resp = await requestMultiOrganDiagnosis({ session_id: sid, organs: [organ] });
      } catch (e) {
        console.error('[workspace-flow] 单部位重跑失败', organ, e);
        setError((e as Error)?.message || `「${organ}」重跑失败`);
        setPhase('concluded'); // 回到结论态，旧结论仍可见，不把医生留在"分析中"
        return;
      }
      // 用最新响应整体替换：只改被重跑的那个部位，其余保持不变（后端按 organs 串行）
      setMultiOrgan((prev) =>
        prev
          ? {
              ...prev,
              organ_statuses: { ...prev.organ_statuses, ...(resp.organ_statuses ?? {}) },
              organ_results: mergeOrganResults(prev.organ_results, resp.organ_results ?? []),
              cross_organ: resp.cross_organ ?? prev.cross_organ,
            }
          : resp
      );
      setOrganResults((prev) => mergeOrganResults(prev, resp.organ_results ?? []));
      setCrossOrgan(pickCrossOrgan(resp.cross_organ) ?? crossOrgan);
      setPhase('concluded');
    },
    [multiOrgan?.session_id, crossOrgan]
  );

  /** 接管已有 request_id —— 只轮询，不重新提交判读。 */
  const resume = useCallback<WorkspaceFlow['resume']>((requestId, tier) => {
    if (!requestId) return;
    setRequestId(requestId);
    setPhase('analyzing');
    setError(null);
    setResult(null);
    setAcknowledged(false);
    setOvertimeNote(null);
    setProgressChars(0);
    setPreviewText(null);
    startedAt.current = Date.now();

    cancelPoll.current?.cancel();
    cancelPoll.current = pollDiagnosisResult(
      requestId,
      (r) => {
        setResult(r);
        setPhase('concluded');
        setProgressChars(0);
        const spent = (Date.now() - startedAt.current) / 1000;
        if (spent * 1000 > P99_MS) {
          setOvertimeNote(`本次耗时 ${spent.toFixed(1)}s，已超基线 4.8s`);
        }
      },
      () => setPhase('timeout'),
      {
        tier: tier ?? DEFAULT_RESPONSE_TIER,
        onStage: () => undefined,
        onProgress: setProgressChars,
        onPreview: setPreviewText,
        onPreviewRefused: () => setPreviewText(null),
      }
    );
  }, []);

  return {
    phase,
    overtimeNote,
    result,
    error,
    requestId,
    progressChars,
    previewText,
    multiOrgan,
    crossOrgan,
    organResults,
    start,
    startMultiOrgan,
    rerunOrgan,
    resume,
    confirm,
    acknowledged,
    setAcknowledged,
    confirming,
    confirmError,
    confirmedAt,
  };
}
