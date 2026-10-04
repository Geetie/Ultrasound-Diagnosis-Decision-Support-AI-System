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
        <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          {children}
        </BrowserRouter>
      </ConfigProvider>
    </ErrorBoundary>
  );
}
