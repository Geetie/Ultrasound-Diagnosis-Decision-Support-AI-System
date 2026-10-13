import { type ReactNode } from 'react';
import { ConfigProvider, theme as antdTheme } from 'antd';
import { BrowserRouter } from 'react-router-dom';
import darkMedicalTheme from '@/styles/theme';
import ErrorBoundary from '@/components/common/ErrorBoundary';

interface ProvidersProps {
  children: ReactNode;
}

export default function Providers({ children }: ProvidersProps) {
  return (
    <ErrorBoundary>
      <ConfigProvider
        theme={{
          ...darkMedicalTheme,
          algorithm: antdTheme.darkAlgorithm,
        }}
      >
        {/*
          basename 必填。2026-10-05 医生视角走查抓到：不设 basename 时
          BrowserRouter 默认 '/'，而产物挂在 `/ui`，于是访问 `/ui/worklist`
          时路由看到的是 pathname `/ui/worklist`，匹配不到任何路由 → 落到
          `<Route path="*" element={<Navigate to="/" replace />} />` → 跳到
          **后端**根 JSON。结果：工作列表 / 新建会话 / 报告 / 设置 等**全部页面
          都打不开**，实测 9 条路由一律回落成同一个工作区（走查证据见
          16 号附录 A.27）。

          BASE_URL 由 Vite 注入：生产（VITE_BASE=/ui/）为 `/ui/`，dev 为 `/`，
          与 vite.config.ts 的 base 保持一致，不用硬编码。
        */}
        {/*
          useTransitions={false} —— 必须显式关闭，否则**医生的导航会静默失效**。

          react-router v7 默认把 location 更新包进 `React.startTransition`。2026-10-13
          临床视角真机走查实测：工作区装载真实多帧影像后，点工作区导航（报告 / 工作列表 /
          设置）**URL 已经变了，但页面永远不切换**（按钮点得动、看起来有反应，实际什么都
          没发生 → 医生被困在工作区，报告入口形同虚设）。取证：
            · 抓 pushState 调用栈确认走的是 react-router 的 `navigate` → `history.push`；
            · 点击后**没有任何路由 chunk 请求**、无 JS 错误、DOM 零变更（MutationObserver
              3s 计数 = 0）、rAF 62fps（排除主线程忙/更新洪水）；
            · **同一状态下普通 lane 的 setState 仍立即渲染**（切测量工具「长度→椭圆」，
              aria-pressed 跟着变）→ 不是 React 卡死，而是 **transition lane 在该页面
              上不会提交**：工作区里 cornerstone 的懒加载/挂起边界一直未结算，React 会为
              transition 无限期等待（非 transition 更新照常提交，只是先亮 Suspense 兜底）；
            · 有头/无头两种浏览器都复现（排除无头伪影）；`location.assign` 整页跳转可用。

          关掉它 → 路由改回同步 setState，导航立即生效；代价仅仅是懒加载 chunk 未就绪时
          先出一次 Suspense 兜底，而这在路由切换里正是期望行为。
          回归：`pytest tests/unit/quality/test_frontend_render_guard.py`（含本开关的守卫）
          与 `node scripts/audit/p26_squad_e2e_walkthrough.mjs` 的 T1-7c（真实点击 + DOM 就绪）。
        */}
        <BrowserRouter
          basename={import.meta.env.BASE_URL.replace(/\/$/, "")}
          useTransitions={false}
        >
          {children}
        </BrowserRouter>
      </ConfigProvider>
    </ErrorBoundary>
  );
}
