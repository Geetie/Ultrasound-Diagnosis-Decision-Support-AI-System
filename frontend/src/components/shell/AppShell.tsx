import type { ReactNode } from 'react';
import AppSidebar, { type NavKey, type ThemeName } from './AppSidebar';

/**
 * 真页面外壳 —— 侧栏 200 + 主区（padding 32 / gap 24）
 *
 * 01 / 08 / 09 / 15 四屏共用。**刻意不套旧的 AppLayout**：
 * 画布上这三个页面自带侧栏、且**没有顶栏也没有状态栏**（契约 §1：连接状态正常时
 * 根本不渲染）。旧 AppLayout 的 Sider 220/240 + Header 56 + Footer 32 与设计
 * 冲突，且会把 900 变成 780 —— 那是工作区的问题，这里同理。
 *
 * 主题按设备级一次设置：根节点 data-theme="light" 即整站换肤，不按页面分别设默认。
 * 15_工作列表浅色因此不需要第二套页面，只换 theme。
 */
export default function AppShell({
  active,
  theme = 'dark',
  screen,
  onNavigate,
  children,
}: {
  /** 侧栏高亮项。09 新建会话传 'worklist' —— 它是工作列表流程的子页。 */
  active: NavKey;
  theme?: ThemeName;
  /** 对应哪个画布帧，挂 data-screen 便于比对。 */
  screen: string;
  onNavigate?: (key: NavKey) => void;
  children: ReactNode;
}) {
  return (
    <div
      className="page-root"
      data-screen={screen}
      data-theme={theme}
    >
      <AppSidebar active={active} theme={theme} onNavigate={onNavigate} />
      <main className="page-main" data-region="page-main">
        {children}
      </main>
    </div>
  );
}
