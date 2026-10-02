export type NavKey = 'worklist' | 'settings';
export type ThemeName = 'dark' | 'light';

/**
 * 侧栏 200 —— 01 / 08 / 09 / 15 四屏逐字同构，只有「高亮项」与「脚注文案」不同。
 *
 * 一级导航**只有 2 项**。进入检查工作区后侧栏收起（工作区不占导航位，
 * 见design_data 的 impl_note）。
 *
 * 高亮规则（此处是易错点，逐条按画布数据）：
 *   · 01_工作列表     → 高亮「工作列表」
 *   · 08_设置         → 高亮「设置」（画布已修正过一次，原先错停在「工作列表」）
 *   · 09_新建检查会话 → **仍高亮「工作列表」**，这是**有意为之**：新建会话是工作列表
 *                      流程的子页，导航反映的是「所在板块」而非「当前子页」。
 *                      画布 `_nav_highlight_decision` 已写明，不要当缺陷「修」。
 *   · 15_工作列表浅色 → 高亮「工作列表」
 *
 * 脚注文案跟随当前主题（01 深色 / 15 浅色），这是 15 与 01 唯一的**文字**差异。
 */
export default function AppSidebar({
  active,
  theme,
  onNavigate,
}: {
  active: NavKey;
  theme: ThemeName;
  onNavigate?: (key: NavKey) => void;
}) {
  return (
    <nav className="page-sidebar" data-region="sidebar" aria-label="一级导航">
      <span className="page-sidebar__brand">超声诊疗决策支持</span>

      <button
        type="button"
        className={
          active === 'worklist'
            ? 'page-sidebar__item page-sidebar__item--active'
            : 'page-sidebar__item'
        }
        aria-current={active === 'worklist' ? 'page' : undefined}
        onClick={() => onNavigate?.('worklist')}
      >
        工作列表
      </button>

      <button
        type="button"
        className={
          active === 'settings'
            ? 'page-sidebar__item page-sidebar__item--active'
            : 'page-sidebar__item'
        }
        aria-current={active === 'settings' ? 'page' : undefined}
        onClick={() => onNavigate?.('settings')}
      >
        设置
      </button>

      {/* 弹性占位：把脚注推到侧栏底部 */}
      <div className="page-sidebar__spacer" />

      <span className="page-sidebar__footnote">
        {`v5 设计稿 · 设备级${theme === 'light' ? '浅色' : '深色'}主题`}
      </span>
    </nav>
  );
}
