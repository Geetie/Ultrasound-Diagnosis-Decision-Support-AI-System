import type { ReactNode } from 'react';

/**
 * 结论区 —— 节点 2:7「结论区-260」
 *
 * 11 个工作区状态共用的区域容器，高度与内部内容解耦：
 *   · 高度由 .ws-conclusion 的 var(--size-conclusion-h)（260）驱动
 *   · 确认动作区出现时收至 var(--size-conclusion-h-compact)（200），见 10_确认动作区
 *   · 04 及 9 个变体：内容顶左对齐（primaryAxis / counterAxisAlignItems = MIN）
 *   · 02 / 03 空态：两轴居中 —— 传 centered
 *
 * 契约要点（17-§1）：本区默认仅医生可见；「患者可见态」不设独立机制，
 * 改为结论区上的「遮蔽 / 临时显示」开关（见 13_诊室遮蔽态）。
 */
export default function WorkspaceConclusion({
  children,
  centered = false,
  compact = false,
}: {
  children: ReactNode;
  /** 02 / 03 空态：两轴居中 */
  centered?: boolean;
  /** 确认动作区出现时收至 200（2:300） */
  compact?: boolean;
}) {
  const cls = [
    'ws-conclusion',
    centered ? 'ws-conclusion--centered' : '',
    compact ? 'ws-conclusion--compact' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <section className={cls} data-region="conclusion">
      {children}
    </section>
  );
}
