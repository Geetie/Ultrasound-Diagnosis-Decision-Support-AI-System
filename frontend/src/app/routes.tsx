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
import { needsDoctorReview, toConclusionView } from '@/services/diagnosisAdapter';

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
  } | null>(null);

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
        });
      })
      .catch(() => {
        if (alive) {
          setSessionInfo({
            id: sessionId,
            organ: null,
            issue: '检查会话读取失败，请回到工作列表重新选择',
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

  /** 影像字节入库 → 立刻发起判读。这就是设计稿里"冻结当前帧即自动发起"的那一步。 */
  const handleUploaded = useCallback(
    async (imageId: string) => {
      if (!sessionId || !organ) return;
      await startFlow({ sessionId, imageIds: [imageId], targetOrgan: organ });
    },
    [sessionId, organ, startFlow]
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
  // 真机路径优先用医生刚选的那份（本地 fileManager 影像 id）；
  // 后端没有 WADO 取回端点，所以刷新后影像区会回到渐变占位 —— 已知限制，不是缺陷。
  const devDicomImageId = dicomRel ? `/dev-dicom/${dicomRel}` : undefined;
  const dicomImageId = upload.state.localImageId ?? devDicomImageId;

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
   */
  if (flow.requestId) {
    const rid = flow.requestId;
    navItems.push({
      key: 'report',
      label: '报告',
      onClick: () => navigate(`/ui/report?request_id=${encodeURIComponent(rid)}`),
    });
  }

  return (
    <PageSuspense>
      <WorkspacePage
        state={state}
        view={view}
        dicomImageId={dicomImageId}
        onConfirm={flow.confirm}
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
