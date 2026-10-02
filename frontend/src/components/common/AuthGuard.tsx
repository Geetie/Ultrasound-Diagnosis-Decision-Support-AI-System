import { Navigate, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';

interface AuthGuardProps {
  children: ReactNode;
}

export default function AuthGuard({ children }: AuthGuardProps) {
  const token = localStorage.getItem('access_token');
  const location = useLocation();

  // 开发旁路：仅在 vite dev 且显式置VITE_SKIP_AUTH=1 时生效。
  // 用途是无后端时也能在真实浏览器里验收医生侧界面（工作区 900px 硬骨架
  // 必须实测getBoundingClientRect，jsdom 测不了）。生产构建中 import.meta.env.DEV
  // 为 false，该分支被静态剔除，不构成鉴权漏洞。
  if (import.meta.env.DEV && import.meta.env.VITE_SKIP_AUTH === '1') {
    return <>{children}</>;
  }

  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}
