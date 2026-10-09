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
import { fetchDicomForDisplay, listSessionImages } from '@/services/imagingService';
import { needsDoctorReview, toConclusionView } from '@/services/diagnosisAdapter';
import ClinicalInfoPanel from '@/components/workspace/ClinicalInfoPanel';
// 值模型/工具在独立文件（react-refresh：组件文件只导出组件，A.38）
import {
  EMPTY_CLINICAL_INFO,
  toClinicalText,
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
          });
        }
      });
    return () => {
      alive = false;
    };
  }, [sessionId]);

  const sessionMatched = !!sessionId && sessionInfo?.id === sessionId;
  const organ = sessionMatched ? sessionInfo.organ : null;
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
  useEffect(() => {
    if (!sessionId) return;
    let alive = true;
    listSessionImages(sessionId)
      .then(async (list) => {
        const last = list.images.at(-1);
        if (!alive || !last) return;
        const id = await fetchDicomForDisplay(last.image_id).catch(() => null);
        // 只存「成功取回」的；取不到就不写 state，保持占位（默认沉默）
        // backendImageId：标注存档 key（A.40），id 字段是 sessionId（按会话匹配）
        if (alive && id)
          setSessionImage({ id: sessionId, imageId: id, backendImageId: last.image_id });
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [sessionId]);
  const sessionImageResolved =
    sessionImage && sessionImage.id === sessionId ? sessionImage.imageId : null;

  /** 影像字节入库 → 立刻发起判读。这就是设计稿里"冻结当前帧即自动发起"的那一步。 */
  const handleUploaded = useCallback(
    async (imageIds: string[]) => {
      if (!sessionId || !organ || imageIds.length === 0) return;
      // A.36 ⑤：临床信息随判读入参上行（symptom/history/labs → clinical_text，
      // 人口学 + SG 条件 → patient_context）。判读发起后面板锁定（见下方 slot）。
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
        },
      });
    },
    [sessionId, organ, startFlow, clinical, sessionInfo]
  );

  const upload = useDicomUpload(sessionId, handleUploaded);

  // 需复核才出现确认动作区。**不能只看 requires_manual_review** ——
  // 真机实测存在 sg_result.allowed=false 而 requires_manual_review=false 的组合
  // （SG 说「请转为人工诊断」）。判定收敛在 diagnosisAdapter.needsDoctorReview()。
  const state: WorkspaceState =
    devState ??
    (flow.phase === 'concluded'
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
  }

  return (
    <PageSuspense>
      <WorkspacePage
      state={state}
      view={view}
      dicomImageId={dicomImageId}
      dicomImageIds={dicomImageIds.length > 1 ? dicomImageIds : undefined}
      annotationArchiveKey={annotationArchiveKey}
      progressChars={flow.progressChars}
        onConfirm={flow.confirm}
        identity={sessionMatched ? sessionInfo.identity ?? undefined : undefined}
        clinicalSlot={
          <ClinicalInfoPanel
            value={clinical}
            onChange={setClinical}
            locked={flow.phase !== 'idle' && flow.phase !== 'scanning'}
          />
        }
        navItems={navItems}
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
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
