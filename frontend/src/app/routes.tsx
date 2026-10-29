import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { Routes, Route, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import AppLayout from '@/components/layout/AppLayout';
import AuthGuard from '@/components/common/AuthGuard';
import LoadingOverlay from '@/components/common/LoadingOverlay';
import WorkspaceImageUpload from '@/components/workspace/WorkspaceImageUpload';
import { WORKSPACE_STATES, type WorkspaceState } from '@/components/workspace/states';
import type { ThemeName } from '@/components/shell/AppSidebar';
import { useWorkspaceFlow } from '@/hooks/useWorkspaceFlow';
import { useDicomUpload } from '@/hooks/useDicomUpload';
import { getSession } from '@/services/sessionService';
import type { SessionInfo } from '@/types/api';
import { organDisplayName } from '@/types/session';
import { reportError } from '@/utils/errorReport';
import VoiceControl from '@/components/voice/VoiceControl';
import { getApiErrorMessage } from '@/utils/apiError';
import { getReportByRequest, signReport } from '@/services/reportService';
import { fetchDicomForDisplay, listSessionImages } from '@/services/imagingService';
import { needsDoctorReview, toConclusionView } from '@/services/diagnosisAdapter';
import ClinicalInfoPanel from '@/components/workspace/ClinicalInfoPanel';
import WorkspaceMultiOrganPanel from '@/components/workspace/WorkspaceMultiOrganPanel';
// 值模型/工具在独立文件（react-refresh：组件文件只导出组件，A.38）
import {
  EMPTY_CLINICAL_INFO,
  toClinicalText,
  toConditionCodes,
  type ClinicalInfoValue,
} from '@/components/workspace/clinicalInfo';

const WorkspacePage = lazy(() => import('@/pages/WorkspacePage'));
const WorklistPage = lazy(() => import('@/pages/WorklistPage'));
const SessionCreatePage = lazy(() => import('@/pages/SessionCreatePage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));
const LoginPage = lazy(() => import('@/pages/LoginPage'));
const QcReportPage = lazy(() => import('@/pages/QcReportPage'));
const KbManagementPage = lazy(() => import('@/pages/KbManagementPage'));
const ConsentPage = lazy(() => import('@/pages/ConsentPage'));
const FeedbackPage = lazy(() => import('@/pages/FeedbackPage'));
const ReportPage = lazy(() => import('@/pages/ReportPage'));
const PatientHistoryPage = lazy(() => import('@/pages/PatientHistoryPage'));

// DiagnosisPage 已按17-医生侧展示契约 §5 裁定 4 删除独立路由：
// LoadingPhase / useProgressiveDiagnosis / reviewAcknowledged 三项能力
// 迁入工作区结论区（随 04_判读态结论、10_确认动作区 一同落地）。
// 页面文件暂留原地未删，其中的判读调用逻辑要在迁移完成后再清理。

function PageSuspense({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<LoadingOverlay visible tip="加载中..." />}>
      {children}
    </Suspense>
  );
}

/**
 * 工作区路由 —— 判读状态由**接口结果**驱动，URL 只作开发期目视验收后门。
 *
 * phase → 视图状态（17 帧 11 个工作区状态中与判读相关的几个）：
 *   idle / scanning  → scanning      02_扫查态
 *   analyzing        → progress      03_等待-分析中（只出「分析中」一词，§3.1）
 *   concluded 且需复核 → actionbar   10_确认动作区（§1：仅有待确认项时出现）
 *   concluded 且无需复核 → conclusion 04_判读态结论
 *   timeout          → progress      超时仍留在分析态，timeout 由 hook 单独报
 *   error            → ai-unavailable 11_AI不可用态
 *
 * `?state=` 只是开发期后门：设计包要求逐屏与参考图比对（三处证据），
 * 而 11 个状态没有各自路由。生产环境不走 URL。
 */
const PHASE_TO_STATE: Record<string, WorkspaceState> = {
  idle: 'scanning',
  scanning: 'scanning',
  analyzing: 'progress',
  timeout: 'progress',
  error: 'ai-unavailable',
};

/** 稳定的空部位列表（供"会话未匹配"时复用，避免每次渲染新建引用）。 */
const NO_ORGANS: string[] = [];

/** 脱敏人口学 → 身份行；全缺 = null（界面显示『患者信息不可用』）。 */
function identityOf(s: SessionInfo): string | null {
  const gender = s.gender === 'female' ? '女' : s.gender === 'male' ? '男' : null;
  const age = s.age != null ? s.age + ' 岁' : null;
  const parts = [s.patient_name_masked ?? null, gender, age].filter(Boolean) as string[];
  return parts.length > 0 ? parts.join(' · ') : null;
}

function WorkspaceRoute() {
  const isDev = import.meta.env.DEV;
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const flow = useWorkspaceFlow();
  const startFlow = flow.start;
  const startMultiOrganFlow = flow.startMultiOrgan;
  const rerunOrganFlow = flow.rerunOrgan;
  /** 正在单独重跑的部位（null = 空闲）。只用于按钮禁用与文案，不影响结论区。 */
  const [rerunningOrgan, setRerunningOrgan] = useState<string | null>(null);
  const handleRerunOrgan = useCallback(
    (organCode: string) => {
      setRerunningOrgan(organCode);
      // rerunOrgan 内部已处理错误（console.error + 界面错误态），finally 只负责复位按钮
      void rerunOrganFlow(organCode).finally(() => setRerunningOrgan(null));
    },
    [rerunOrganFlow]
  );
  /**
   * 报告侧面板（07 draft / 12 signed）的生产可达（T7）。
   *
   * 此前 `report` / `signed` 两态**只有 dev 的 `?state=` 后门**能到。
   * 现在：判读拿到 request_id 后常驻条出现「报告面板」入口，点了就在工作区就地开面板。
   *
   * 两个关键口径：
   *   1. **面板正文必须是后端真数据**（`ReportResponse.rendered`）。原组件只有画布示意
   *      文字（写死「18.4 × 12.1 mm 低回声结节」），直接放开 = 在真实患者界面上显示
   *      伪造的超声所见。所以进入面板时先查报告；查不到就**如实说明**没有草稿，
   *      让医生去 `/report` 页生成，绝不显示假所见。
   *   2. **signed 只由后端 `signature_present` 驱动**，不由 UI 猜。
   */
  const [reportPanel, setReportPanel] = useState<'draft' | 'signed' | null>(null);
  const [reportText, setReportText] = useState<string | null>(null);
  const [reportStatusNote, setReportStatusNote] = useState<string | null>(null);
  // 2026-10-28（P0-1）：报告 ID 必须留下来 —— 面板上的「签发」要调
  // `POST /report/{report_id}/sign`，而原先只存了正文与状态，没有 id → 无从签发。
  const [reportId, setReportId] = useState<string | null>(null);
  const [signing, setSigning] = useState(false);

  const openReportPanel = useCallback((rid: string) => {
    setReportPanel('draft'); // 先开面板（立即有反馈），随后用真实状态纠正
    setReportText(null);
    setReportId(null);
    setReportStatusNote('正在读取报告…');
    getReportByRequest(rid)
      .then((r) => {
        setReportPanel(r.signature_present ? 'signed' : 'draft');
        // 只有后端真的渲染过正文才显示；否则如实说明（空态由面板负责渲染）
        setReportText(r.rendered ?? null);
        setReportId(r.report_id ?? null);
        setReportStatusNote(
          r.rendered
            ? null
            : '该报告尚无渲染正文 —— 请到「报告」页生成草稿'
        );
      })
      .catch((err: unknown) => {
        // 零静默：尚未生成草稿是常见状态（404），不是故障；技术细节留痕、界面说人话
        console.error('[workspace] 报告读取失败（面板不显示任何正文）', err);
        setReportPanel('draft');
        setReportText(null);
        setReportId(null);
        setReportStatusNote(
          getApiErrorMessage(err, '尚未生成报告草稿 —— 请到「报告」页生成')
        );
      });
  }, []);

  /**
   * 工作区内签发（P0-1 修复，2026-10-28）。
   *
   * 为什么必须在这里做：面板上的「签发」原先 `onSign` 未传 → `onClick={undefined}`，
   * 点了完全没反应。现在真调 `POST /report/{report_id}/sign`。
   *
   * 三个口径：
   *   1. **签名对象是后端渲染正文**，前端只发空 body（operator 由后端从 token 取），
   *      不在前端拼任何内容 —— 签什么由服务端决定，这是法律责任节点；
   *   2. **缺段拒签（E1030）如实透传**：后端会拒，前端把中文段落名显示出来，
   *      不吞成"签发失败"；
   *   3. **成功后回读后端状态**（`signature_present` 驱动 signed），不由 UI 猜。
   */
  const handleSignReport = useCallback(async () => {
    if (!reportId || signing) return;
    setSigning(true);
    setReportStatusNote('正在签发…');
    try {
      const signed = await signReport(reportId);
      setReportPanel(signed.signature_present ? 'signed' : 'draft');
      setReportText(signed.rendered ?? null);
      setReportStatusNote(
        signed.signature_present
          ? `已签发（密钥 ${signed.signing_key_name ?? '未知'}）`
          : '服务端未返回签名，请到「报告」页核对'
      );
    } catch (err: unknown) {
      // E1030（缺段拒签）与其它失败一律如实显示后端中文文案
      reportError('workspace', '工作区内签发失败', err);
      setReportStatusNote(getApiErrorMessage(err, '签发失败，请到「报告」页处理'));
    } finally {
      setSigning(false);
    }
  }, [reportId, signing]);

  /**
   * 「结构化校对」（P0-2 修复，2026-10-28）。
   *
   * **这一条不是"没接线"，是"没实现"**：设计文档（14-前端设计评审结论 §263）
   * 定义了一个「与草稿互切的选词面板」，但仓库里**从来没有这个组件**
   * （`grep 结构化校对` 只命中本按钮文案与设计文档）。
   *
   * 因此本轮**不伪造**一个假的校对面板，而是把按钮接到**唯一真实存在的校对能力**上：
   * `/report` 页的「补录缺失段落」面板（`ReportPage.tsx` 的 supplement-panel，
   * 按后端 `completeness.missing` 逐段给输入框、保存后重渲染）。这是同一件事的
   * 现有实现（"改哪句点哪句"的最小可用版），且带着 request_id 跳过去，医生不用手敲。
   *
   * 遗留：真正的"结构化选词面板"（按 TI-RADS 五维度勾选、无关段落自动折叠）
   * 仍未实现 —— 已登记到修复报告，属需设计/产品排期的功能缺口，不是接线问题。
   */
  const handleStructuredProofread = useCallback(() => {
    const rid = flow.requestId;
    if (!rid) {
      setReportStatusNote('尚未发起判读，无法校对 —— 请先完成一次判读');
      return;
    }
    navigate(`/report?request_id=${encodeURIComponent(rid)}`);
  }, [flow.requestId, navigate]);

  /**
   * 「下一个病人」（P0-3 修复，2026-10-28）。
   *
   * 签发后回工作列表并清屏。清屏靠**换掉 URL 上的 session_id / request_id**
   * —— 工作区的数据全部由 URL 上的 id 驱动（`sessionInfo` / `sessionImage` 都按 id
   * 匹配，对不上就不渲染），所以离开这一屏就等于零残留，不需要额外的全局 reset。
   */
  const handleSignNext = useCallback(() => {
    setReportPanel(null);
    setReportText(null);
    setReportId(null);
    setReportStatusNote(null);
    navigate('/worklist');
  }, [navigate]);

  // 真机结果 → 结论视图模型；null = 还没有可用结论（组件退回设计预览文案）
  const view = toConclusionView(flow.result);

  const raw = params.get('state');
  const devState =
    isDev && raw && (WORKSPACE_STATES as readonly string[]).includes(raw)
      ? (raw as WorkspaceState)
      : null;

  /**
   * 检查会话 —— 由工作列表的「继续 / 查看」带 `?session_id=` 进来。
   *
   * 上传端点与判读端点都以 session_id 为外键，所以它是「装载影像」的前置条件。
   * **取不到部位就不给上传入口**：判读必须带 target_organ，
   * 凭空顶一个甲状腺上去等于让系统对不对的部位下结论。
   */
  const sessionId = params.get('session_id') || undefined;
  // 刻意不在 effect 里同步清空 state：那会触发级联渲染（react-hooks/set-state-in-effect）。
  // 改成「按 id 匹配」——渲染期拿 sessionInfo.session_id 和当前 URL 比，
  // 对不上就当作没有部位，于是换会话时不会出现上一位患者的部位闪一下。
  const [sessionInfo, setSessionInfo] = useState<{
    id: string;
    organ: string | null;
    issue: string | null;
    identity: string | null;
    demo: { age: number | null; gender: string | null };
    /** 会话起始时间（ISO）—— 影像区时间角标用。影像采集时间本系统未解析，不冒充。 */
    startedAt: string | null;
    /**
     * 会话声明的**全部**目标部位（后端 `target_organs`）。
     * 为什么需要：`organ` 只取第一个部位用于单器官判读；而"多部位会话该走多器官端点"
     * 这个判断必须看全量部位数。只留 first 的话，3 部位的会话会被当成单器官跑，
     * 另两个部位永远不会产出结论（正是本任务要修的可达性缺口）。
     */
    targetOrgans: string[];
  } | null>(null);
  const [clinical, setClinical] = useState<ClinicalInfoValue>(EMPTY_CLINICAL_INFO);

  useEffect(() => {
    if (!sessionId) return;
    let alive = true;
    getSession(sessionId)
      .then((s) => {
        if (!alive) return;
        const first = s.target_organs?.[0];
        setSessionInfo({
          id: sessionId,
          organ: first ?? null,
          issue: first ? null : '该检查会话未声明部位，无法发起判读',
          // A.36 P0：身份随会话数据驱动（服务端已脱敏），写死人名 = 患者身份错显
          identity: identityOf(s),
          demo: { age: s.age ?? null, gender: s.gender ?? null },
          startedAt: s.created_at ?? null,
          targetOrgans: s.target_organs ?? [],
        });
      })
      .catch(() => {
        if (alive) {
          setSessionInfo({
            id: sessionId,
            organ: null,
            issue: '检查会话读取失败，请回到工作列表重新选择',
            identity: null,
            demo: { age: null, gender: null },
            startedAt: null,
            targetOrgans: [],
          });
        }
      });
    return () => {
      alive = false;
    };
  }, [sessionId]);

  const sessionMatched = !!sessionId && sessionInfo?.id === sessionId;
  const organ = sessionMatched ? sessionInfo.organ : null;
  /**
   * 会话的全部目标部位（未匹配 = `NO_ORGANS`，不猜）。
   *
   * 用**模块级常量**而不是字面量 `[]`：这个值进了 handleUploaded 的依赖数组，
   * 每次渲染新建空数组会让那个 useCallback 每次重建（exhaustive-deps 警告）。
   */
  const targetOrgans = sessionMatched ? sessionInfo.targetOrgans : NO_ORGANS;
  /** 多部位会话：走多器官端点（后端要求 ≥2 份独立结论才有跨器官核对）。 */
  const isMultiOrgan = targetOrgans.length >= 2;
  const sessionIssue = sessionMatched ? sessionInfo.issue : null;

  /**
   * 会话里已有的最近影像 —— 「继续检查」时恢复显示。
   *
   * 这就是「刷新后影像回占位」的修复：session_id 在 URL 上不会丢，
   * 所以按会话列出影像 → 取最新一份 → 走服务端副本显示。取不到就保持
   * 占位底色（默认沉默），**绝不**显示"上一位患者"的图。
   */
  // 刻意不在 effect 里同步 setState(null)：那会触发级联渲染（react-hooks 报 error）。
  // 与上面 sessionInfo 同一范式 ——「按 id 匹配」：会话对不上时渲染期天然回落成
  // null（占位底色），换会话不会闪出上一位患者的图。
  const [sessionImage, setSessionImage] = useState<{
    id: string;
    imageId: string;
    backendImageId: string;
  } | null>(null);
  // A.58（零静默 · 渲染层）：取不到影像字节时**必须说出来**。
  // 原实现是「只存成功取回的；取不到就不写 state，保持占位（默认沉默）」——
  // 真机 p28 反证腿实测：屏蔽 `/api/v1/imaging/**` 后，影像区照显**设计预览态的假标注框**
  //（结节 A/B、18.4×12.1mm）且零提示，医生完全不知道影像没取回来（A.35 同族形态）。
  const [sessionImageIssue, setSessionImageIssue] = useState<{
    id: string;
    reason: string;
  } | null>(null);
  useEffect(() => {
    if (!sessionId) return;
    let alive = true;
    listSessionImages(sessionId)
      .then(async (list) => {
        const last = list.images.at(-1);
        if (!alive || !last) return; // 该会话还没有影像：不是故障，不提示
        const id = await fetchDicomForDisplay(last.image_id).catch((err: unknown) => {
          // A.60：医生可见出口在下面（`data-region="image-load-error"`，非静默）；
          // 这里补技术留痕，走统一出口便于将来接采集。
          reportError('workspace', '影像字节取回失败（本次检查的影像将不可显示）', err);
          return null;
        });
        if (!alive) return;
        if (id) {
          // backendImageId：标注存档 key（A.40），id 字段是 sessionId（按会话匹配）
          setSessionImage({ id: sessionId, imageId: id, backendImageId: last.image_id });
          setSessionImageIssue(null);
          return;
        }
        setSessionImageIssue({ id: sessionId, reason: '影像字节取回失败' });
      })
      .catch((err: unknown) => {
        if (!alive) return;
        // 技术细节进控制台（医生可见文案保持可读，沿用 A.56 的可读降级口径）
        reportError('workspace', '会话影像列表读取失败', err);
        setSessionImageIssue({ id: sessionId, reason: '影像列表读取失败' });
      });
    return () => {
      alive = false;
    };
  }, [sessionId]);
  const sessionImageIssueResolved =
    sessionImageIssue && sessionImageIssue.id === sessionId ? sessionImageIssue.reason : null;
  const sessionImageResolved =
    sessionImage && sessionImage.id === sessionId ? sessionImage.imageId : null;

  /**
   * 影像字节入库 → 立刻发起判读。这就是设计稿里"冻结当前帧即自动发起"的那一步。
   *
   * 两条分支（T-GB-AGT-01）：
   *   · 会话 `target_organs` ≥ 2 → **多器官端点**（一次跑完全部部位，
   *     并拿到交叉器官冲突/一致性结果；这两条防线需要 ≥2 份独立结论）；
   *   · 单部位 → 原来的单器官端点（行为不变）。
   * 未走多器官的会话里，第 2、3 个部位永远不会产出结论 —— 这正是本次要修的可达性缺口。
   */
  const handleUploaded = useCallback(
    async (imageIds: string[]) => {
      if (!sessionId || !organ || imageIds.length === 0) return;

      if (isMultiOrgan) {
        // ⚠️ 已知限制（如实记录，不粉饰）：多器官端点 `MultiOrganDiagnosisRequest`
        // 没有 clinical_text / patient_context / image_ids 字段（app/models/schemas.py:785），
        // 它从**同会话已有请求**继承这三项（orchestrator.py:697-711 的 donor 逻辑）。
        // 所以本会话若从未发过单器官请求，医生这次填的临床信息不会进 SG 的判据。
        // 不改后端的前提下只能如实暴露这一点；已在 task 报告里登记。
        await startMultiOrganFlow({
          sessionId,
          organs: targetOrgans,
        });
        return;
      }

      // A.36 ⑤：临床信息随判读入参上行（symptom/history/labs → clinical_text，
      // 人口学 + SG 条件 → patient_context）。判读发起后面板锁定（见下方 slot）。
      //
      // conditions 发**规范码**不发中文标签：SG 的别名表只认 pregnancy /
      // contrast_allergy / anticoagulant_therapy，送「妊娠」进去命中数为 0
      // （勾了禁忌也不拦）。码表唯一来源 clinicalInfo.CONDITION_CODES。
      //
      // ⚠️ 一项都没勾时也必须送 `[]`（已确认无条件），不能省成 undefined：
      // undefined = "未提供" → SG 走保守分支「缺数据即判命中」→ 每次判读都要求
      // 人工复核，自动结论永远出不来。toConditionCodes([]) 返回 []，不会返回 undefined。
      await startFlow({
        sessionId,
        imageIds,
        targetOrgan: organ,
        clinicalText: toClinicalText(clinical),
        patientContext: {
          age: sessionInfo?.demo.age ?? undefined,
          gender:
            sessionInfo?.demo.gender === 'female'
              ? 'F'
              : sessionInfo?.demo.gender === 'male'
                ? 'M'
                : undefined,
          conditions: toConditionCodes(clinical.conditions),
        },
      });
    },
    [
      sessionId,
      organ,
      isMultiOrgan,
      targetOrgans,
      startFlow,
      startMultiOrganFlow,
      clinical,
      sessionInfo,
    ]
  );

  const upload = useDicomUpload(sessionId, handleUploaded);

  // 需复核才出现确认动作区。**不能只看 requires_manual_review** ——
  // 真机实测存在 sg_result.allowed=false 而 requires_manual_review=false 的组合
  // （SG 说「请转为人工诊断」）。判定收敛在 diagnosisAdapter.needsDoctorReview()。
  //
  // 多部位（T-GB-AGT-01）：多器官结果出来后进入 05/14 两态 ——
  //   · 任一部分失败/未执行 → `organ-tags`（标签排逐部位交代，含重跑入口）
  //   · 全部完成           → `multi-organ`（部位指示条 + 结论按部位给出）
  // 单部位会话完全不进这两态（行为不变）。dev 后门（?state=）优先级仍最高。
  //
  // ⚠️ 多部位（T-GB-AGT-01）与报告侧面板（T7）都在这条优先级链里：
  //   dev 后门 > 报告面板（收尾动作，独占结论区） > 多部位 > 单部位判读态。
  // 单部位会话不传/不进 reportPanel 时行为完全不变。
  const multiOrganIncomplete =
    flow.organResults.length > 0 &&
    flow.organResults.some((r) => r.status === 'failed' || r.status === 'skipped');
  const state: WorkspaceState =
    devState ??
    (reportPanel
      ? reportPanel === 'signed'
        ? 'signed'
        : 'report'
      : flow.organResults.length >= 2
        ? multiOrganIncomplete
          ? 'organ-tags'
          : 'multi-organ'
        : flow.phase === 'concluded'
          ? needsDoctorReview(flow.result)
            ? 'actionbar'
            : 'conclusion'
          : (PHASE_TO_STATE[flow.phase] ?? 'scanning'));

  // ?dicom=<相对路径> 仅 dev：把真实超声 DICOM 灌进影像区，替换渐变占位。
  // 用来验证 AGENT_TASKS §九 标注的「叠加标注与真实组织纹理的对比度从未验证」。
  const dicomRel = isDev ? params.get('dicom') : null;
  const devDicomImageId = dicomRel ? `/dev-dicom/${dicomRel}` : undefined;
  /**
   * 影像区显示源的优先级：
   *   1. 刚上传的**服务端副本**（同管线同源，且证明 round trip）——多帧时整栈
   *   2. 刚上传的浏览器本地副本（服务端回取失败时的回落）——多帧时整栈
   *   3. 本会话最近一份已存影像（刷新 / 「继续检查」后恢复，单张）
   *   4. dev 后门（?dicom=）
   * 都没有 → 渐变占位（默认沉默）。
   *
   * A.38 多帧分期①：显示栈 = 同优先级下的整批副本（顺序=上传顺序）。
   * dicomImageId 保留栈首（旧签名兼容 + 判读发起处使用）。
   */
  const uploadedServerStack = upload.state.serverImageIds ?? [];
  const uploadedLocalStack = upload.state.localImageIds ?? [];
  // 测量注释存档 key（A.40）：后端 image_id（img_/frame_ 前缀）。会话恢复链路
  // 的后端 id 在 sessionImage（id 字段），上传链路取判读用的第一张 image_id。
  const annotationArchiveKey =
    upload.state.imageIds?.[0] ?? sessionImage?.backendImageId;
  const dicomImageIds: string[] =
    uploadedServerStack.length > 0
      ? uploadedServerStack
      : uploadedLocalStack.length > 0
        ? uploadedLocalStack
        : sessionImageResolved
          ? [sessionImageResolved]
          : devDicomImageId
            ? [devDicomImageId]
            : [];
  const dicomImageId = dicomImageIds[0];
  /**
   * A.52 帧归属：与 dicomImageIds **逐位对齐**的后端 image_id。
   *
   * 显示栈里是 Cornerstone 句柄（`dicomfile:N?frame=k`），分割结果自带的是后端
   * `img_...`；门控必须比后端 id，否则永不相等 → 掩膜任何帧都不显示（A.52 走查实测）。
   * 会话恢复 / dev 后门这两条单张路径，归属就是那张影像的后端 id（取不到则空串，
   * 门控退化为「只比帧号」，与 A.51 原语义一致）。
   */
  const dicomFrameOwners: string[] =
    uploadedServerStack.length > 0
      ? (upload.state.serverFrameOwners ?? [])
      : uploadedLocalStack.length > 0
        ? (upload.state.localFrameOwners ?? [])
        : dicomImageIds.length > 0
          ? [annotationArchiveKey ?? '']
          : [];

  // ?request_id=… —— 语音控件发起判读后带着它跳过来（AppLayout 的 onExecuted）。
  // 那个请求已经发出并落库，所以走 resume() **只轮询取结果**，绝不重发判读。
  const requestId = params.get('request_id');
  useEffect(() => {
    if (requestId) flow.resume(requestId);
    // 只在 requestId 变化时接管；flow 身份稳定，不进依赖以免重复订阅
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestId]);

  // 工作区是全站唯一没有侧栏的页面（通栏 900 骨架）。不给出口，医生登录后就困在这里
  // —— 2026-10-05 真浏览器走查实测 /ui/ 的 btn=[]，10 个导航项一个都点不到。
  const navItems = [
    { key: 'worklist', label: '工作列表', onClick: () => navigate('/worklist') },
    { key: 'session-new', label: '新建会话', onClick: () => navigate('/session/new') },
    { key: 'settings', label: '设置', onClick: () => navigate('/settings') },
  ];

  /**
   * 「报告」入口 —— **仅在判读已发起后出现**（默认沉默）。
   *
   * 为什么必须有：`/ui/report` 靠 URL 上的 `request_id` 取报告（`ReportPage` 只认这个
   * 参数），而 request_id 是发起判读时后端给的。以前工作区**完全没有**通往报告的入口
   * （真机实测：判读跑完后按钮仍只有那三个导航项），医生只能手敲 URL 才能写报告、
   * 签名 —— 医生流程的最后一环是断的。设计稿里的工作区报告侧面板（07 / 12 屏）靠
   * `onSign` / `onStructuredProofout`，但路由层从未传，状态 `report` / `signed` 不可达。
   *
   * 为什么放常驻条而不是结论区：结论区一级只给三样（结论 + 恶性风险 + 建议动作），
   * 往里塞导航会破坏那条口径；常驻条高度 40 不变，900 骨架不受影响。
   *
   * ⚠️ 路径**不带 `/ui` 前缀**，与上面三个导航项同一约定：`BrowserRouter` 的
   * basename 已经是 `/ui`，`navigate()` 会自动补上。写成 `/ui/report` 会变成
   * `/ui/ui/report` → 路由匹配不到 → `path="*"` 把医生悄悄弹回工作区 ——
   * 按钮点得动、看起来有反应，实际什么都没发生（真机走查实测）。
   */
  if (flow.requestId) {
    const rid = flow.requestId;
    navItems.push({
      key: 'report',
      label: '报告',
      onClick: () => navigate(`/report?request_id=${encodeURIComponent(rid)}`),
    });
    // 报告侧面板入口（T7）：07/12 两态此前只有 dev `?state=` 可达。
    // 与上面「报告」导航项分工：那个跳独立报告页（完整编辑/补录/签发），
    // 这个就地开侧面板（边看结论边看报告草稿，不离开工作区）。
    // 面板正文只显示后端真实正文（见 openReportPanel），无草稿时如实说明。
    navItems.push({
      key: 'report-panel',
      label: reportPanel ? '收起报告面板' : '报告面板',
      onClick: () => (reportPanel ? setReportPanel(null) : openReportPanel(rid)),
    });
  }

  /**
   * 停档（06）的真实入口：**不提供**，原因如下（不是没做，是不该做）。
   *
   * `stopped` 的语义是「禁忌命中，不得签发」，它的唯一合法数据来源是 SG 判读结果
   * （`view.safetyBlocked` / `sg_result.allowed === false`），不是可手动置位的开关。
   * 生产上它**已经**由真实数据驱动：`WorkspaceConclusionBody` 在任一判读态下检测到
   * `safetyBlocked` 就渲染 `WorkspaceStopAlert`（契约 §4「不可削减」）。
   *
   * 至于会话终止（`SessionStatus.TERMINATED`）：后端**没有**独立终止端点，只能
   * `PATCH /api/v1/sessions/{id}` 带 `{status:'terminated'}`（`sessionService.endSession`）。
   * 该方法前端已有且有调用方（`useSession.handleEndSession`），但它的语义是
   * 「主动结束检查」，**不是**「禁忌停档」。把它接到 06 屏 = 让医生以为系统判了禁忌，
   * 实际只是自己中止了检查 —— 属于伪造临床结论，明确不做。
   */

  return (
    <PageSuspense>
      <WorkspacePage
      state={state}
      view={view}
      dicomImageId={dicomImageId}
      dicomImageIds={dicomImageIds.length > 0 ? dicomImageIds : undefined}
      dicomFrameOwners={dicomFrameOwners}
      annotationArchiveKey={annotationArchiveKey}
      progressChars={flow.progressChars}
      generationPreview={flow.previewText}
      declaredOrgan={organDisplayName(organ) ?? undefined}
      sessionStartedAt={sessionMatched ? sessionInfo.startedAt ?? undefined : undefined}
      imageLoadIssue={
        dicomImageIds.length === 0 ? (sessionImageIssueResolved ?? undefined) : undefined
      }
        onConfirm={flow.confirm}
        /* P0-4：确认回写的三态（进行中 / 已确认 / 失败）必须让医生看见 */
        confirming={flow.confirming}
        confirmedAt={flow.confirmedAt}
        confirmError={flow.confirmError}
        identity={sessionMatched ? sessionInfo.identity ?? undefined : undefined}
        clinicalSlot={
          <ClinicalInfoPanel
            value={clinical}
            onChange={setClinical}
            locked={flow.phase !== 'idle' && flow.phase !== 'scanning'}
          />
        }
        navItems={navItems}
        onCollapseReport={() => setReportPanel(null)}
        reportText={reportText}
        reportStatusNote={reportStatusNote}
        /* 2026-10-28：三个原先"从未传入"的回调，现在全部接上真实实现 */
        onSign={handleSignReport}
        signing={signing}
        onStructuredProofread={handleStructuredProofread}
        onSignNext={handleSignNext}
        /* P0-5：回读文案改由真实结论驱动（见 WorkspaceActionBar 的 readback 参数） */
        readback={view?.headline ?? null}
        organCount={targetOrgans.length}
        multiOrganSlot={
          flow.organResults.length > 0 ? (
            <WorkspaceMultiOrganPanel
              results={flow.organResults}
              crossOrgan={flow.crossOrgan}
              rerunningOrgan={rerunningOrgan}
              onRerunOrgan={handleRerunOrgan}
            />
          ) : undefined
        }
        uploadSlot={
          <WorkspaceImageUpload
            ready={!!sessionId && !!organ}
            blockedReason={
              sessionIssue ??
              (sessionId ? '正在读取检查会话…' : '尚未选择检查会话 —— 请从工作列表选择一次检查')
            }
            state={upload.state}
            onPick={upload.handleFile}
            onGoWorklist={() => navigate('/worklist')}
          />
        }
      />
      {/* A.58 口径落地：语音入口进主流程。
          原先 `VoiceControl` 只挂在 `AppLayout`，而主流程四页**刻意不套** AppLayout
          → 医生在工作区根本没有语音入口（真机 p26 的 T1-3 如实记录过）。
          这里按页面级挂载（组件自带 fixed 定位，不影响 900 骨架）。
          ⚠️ 会话 id **显式传参**，不往全局 sessionStore 写：写了会让 AppLayout 的
          nav WebSocket 消费者误判"会话就绪"而在别的页面上发起连接（p26 T0-err 抓到）。 */}
      <VoiceControl
        sessionId={sessionId}
        onExecuted={(result) => {
          if (result.request_id) {
            navigate(`/?request_id=${encodeURIComponent(result.request_id)}`);
          }
        }}
      />
    </PageSuspense>
  );
}

/**
 * 工作列表路由。
 *
 * 15_工作列表浅色**不是第二个页面**（_页面分类.md：与 01 逐节点对照，只有颜色变、
 * 布局一个像素没动），换 data-theme 即得，所以这里只传 theme。
 * 生产环境主题由设备级设置决定（见 08_设置「跟随设备」），故 ?theme= 仅 dev 生效。
 */
function WorklistRoute() {
  const isDev = import.meta.env.DEV;
  const [params] = useSearchParams();
  const theme: ThemeName =
    isDev && params.get('theme') === 'light' ? 'light' : 'dark';

  return (
    <PageSuspense>
      <WorklistPage theme={theme} />
    </PageSuspense>
  );
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<PageSuspense><LoginPage /></PageSuspense>} />

      {/* ===== 设计包覆盖的 4 条路由：01/08/09 + 11 个工作区状态 =====
          全部**刻意不套 AppLayout**：
            · 三个真页面自带侧栏 200，且画布上没有顶栏/状态栏
              （契约 §1：连接状态正常时根本不渲染）
            · 工作区是通栏，40+600+260=900 的硬骨架放不进
              AppLayout 的 Sider+Header+Footer（那样只剩 780）*/}
      <Route
        path="/"
        element={
          <AuthGuard>
            <WorkspaceRoute />
          </AuthGuard>
        }
      />
      <Route
        path="/worklist"
        element={
          <AuthGuard>
            <WorklistRoute />
          </AuthGuard>
        }
      />
      <Route
        path="/settings"
        element={
          <AuthGuard>
            <PageSuspense><SettingsPage /></PageSuspense>
          </AuthGuard>
        }
      />
      <Route
        path="/session/new"
        element={
          <AuthGuard>
            <PageSuspense><SessionCreatePage /></PageSuspense>
          </AuthGuard>
        }
      />

      {/* ===== 设计包未覆盖的遗留路由 =====
          质控/ 知识库 / 同意 / 反馈 四页不在 15 帧里，保持原有 AppLayout 外框，
          等各自的设计数据到位后再按同一套规则重做。*/}
      <Route element={<AuthGuard><AppLayout /></AuthGuard>}>
        <Route path="/qc-report" element={<PageSuspense><QcReportPage /></PageSuspense>} />
        <Route path="/kb-management" element={<PageSuspense><KbManagementPage /></PageSuspense>} />
        <Route path="/consent" element={<PageSuspense><ConsentPage /></PageSuspense>} />
        <Route path="/feedback" element={<PageSuspense><FeedbackPage /></PageSuspense>} />
        <Route path="/report" element={<PageSuspense><ReportPage /></PageSuspense>} />
        {/* 第二梯队③ 历史对比阅片（A.54）：?patient_id=… 打开该患者的历史检查对比 */}
        <Route path="/history" element={<PageSuspense><PatientHistoryPage /></PageSuspense>} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
