import { useCallback, useEffect, useRef, useState } from 'react';
import {
  pollDiagnosisResult,
  requestDiagnosis,
} from '@/services/diagnosisService';
import type { DiagnosisResult } from '@/types/api';

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
  /** 发起判读（冻结后自动触发，不需要医生点按钮）。 */
  start: (input: { sessionId: string; imageIds: string[]; targetOrgan: string; tier?: 'realtime' | 'standard' | 'full' }) => Promise<void>;
  /** 「确认结论」三选一等效里的点按钮那条（说「确认」/ 脚踏走各自的通道）。 */
  confirm: () => void;
  /** 「已人工核验」勾选，对应 reviewAcknowledged。 */
  acknowledged: boolean;
  setAcknowledged: (v: boolean) => void;
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

export function useWorkspaceFlow(): WorkspaceFlow {
  const [phase, setPhase] = useState<FlowPhase>('idle');
  const [overtimeNote, setOvertimeNote] = useState<string | null>(null);
  const [result, setResult] = useState<DiagnosisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);

  const startedAt = useRef<number>(0);
  const cancelPoll = useRef<{ cancel: () => void } | null>(null);

  useEffect(() => () => cancelPoll.current?.cancel(), []);

  const start = useCallback<WorkspaceFlow['start']>(async (input) => {
    setPhase('analyzing');
    setError(null);
    setResult(null);
    setAcknowledged(false);
    setOvertimeNote(null);
    startedAt.current = Date.now();

    let requestId: string;
    try {
      const resp = await requestDiagnosis({
        session_id: input.sessionId,
        image_ids: input.imageIds,
        target_organ: input.targetOrgan,
        response_tier: input.tier ?? 'standard',
        // 幂等键：留空由后端按会话去重，避免前端每次生成随机 key 造成永不命中
      } as never);
      requestId = (resp as { request_id?: string }).request_id ?? '';
    } catch (e) {
      setError((e as Error)?.message || '判读请求失败');
      setPhase('error');
      return;
    }

    if (!requestId) {
      setError('后端未返回 request_id');
      setPhase('error');
      return;
    }

    cancelPoll.current?.cancel();
    cancelPoll.current = pollDiagnosisResult(
      requestId,
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
        tier: input.tier ?? 'standard',
        // 阶段事件只用于埋点/超基线判断，不进界面（⛔ 不许把 6 个阶段名渲染给医生）
        onStage: () => undefined,
      }
    );
  }, []);

  const confirm = useCallback(() => {
    if (phase !== 'concluded') return;
    setAcknowledged(true);
  }, [phase]);

  /** 接管已有 request_id —— 只轮询，不重新提交判读。 */
  const resume = useCallback<WorkspaceFlow['resume']>((requestId, tier) => {
    if (!requestId) return;
    setPhase('analyzing');
    setError(null);
    setResult(null);
    setAcknowledged(false);
    setOvertimeNote(null);
    startedAt.current = Date.now();

    cancelPoll.current?.cancel();
    cancelPoll.current = pollDiagnosisResult(
      requestId,
      (r) => {
        setResult(r);
        setPhase('concluded');
        const spent = (Date.now() - startedAt.current) / 1000;
        if (spent * 1000 > P99_MS) {
          setOvertimeNote(`本次耗时 ${spent.toFixed(1)}s，已超基线 4.8s`);
        }
      },
      () => setPhase('timeout'),
      { tier: tier ?? 'standard', onStage: () => undefined }
    );
  }, []);

  return {
    phase,
    overtimeNote,
    result,
    error,
    start,
    resume,
    confirm,
    acknowledged,
    setAcknowledged,
  };
}
