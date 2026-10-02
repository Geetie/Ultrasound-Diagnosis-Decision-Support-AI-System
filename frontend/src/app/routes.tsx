import { lazy, Suspense, useEffect } from 'react';
import { Routes, Route, Navigate, useSearchParams } from 'react-router-dom';
import AppLayout from '@/components/layout/AppLayout';
import AuthGuard from '@/components/common/AuthGuard';
import LoadingOverlay from '@/components/common/LoadingOverlay';
import { WORKSPACE_STATES, type WorkspaceState } from '@/components/workspace/states';
import type { ThemeName } from '@/components/shell/AppSidebar';
import { useWorkspaceFlow } from '@/hooks/useWorkspaceFlow';
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
  const flow = useWorkspaceFlow();

  // 真机结果 → 结论视图模型；null = 还没有可用结论（组件退回设计预览文案）
  const view = toConclusionView(flow.result);

  const raw = params.get('state');
  const devState =
    isDev && raw && (WORKSPACE_STATES as readonly string[]).includes(raw)
      ? (raw as WorkspaceState)
      : null;

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
  const dicomImageId = dicomRel ? `/dev-dicom/${dicomRel}` : undefined;

  // ?request_id=… —— 语音控件发起判读后带着它跳过来（AppLayout 的 onExecuted）。
  // 那个请求已经发出并落库，所以走 resume() **只轮询取结果**，绝不重发判读。
  const requestId = params.get('request_id');
  useEffect(() => {
    if (requestId) flow.resume(requestId);
    // 只在 requestId 变化时接管；flow 身份稳定，不进依赖以免重复订阅
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestId]);

  return (
    <PageSuspense>
      <WorkspacePage
        state={state}
        view={view}
        dicomImageId={dicomImageId}
        onConfirm={flow.confirm}
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
