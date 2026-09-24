import type { ReactNode } from 'react';
import { Skeleton } from 'antd';
import { tokens } from '@/styles/tokens';

export interface ProgressiveBlockProps {
  /** 是否已揭示（false 时显示骨架，占位高度与真实内容接近以免跳动） */
  ready: boolean;
  /** 骨架行数 */
  skeletonRows?: number;
  /** 无障碍：说明该区块内容（骨架屏期间读屏可达） */
  label: string;
  children: ReactNode;
}

/**
 * 渐进式揭示容器（T-GB-FE-01）。
 *
 * 骨架屏期间用固定高度占位，避免"内容到达 → 布局跳动 → 医生视线丢失"。
 * 揭示时使用 220ms 上浮动效，节奏与 tokens.duration 一致。
 */
export default function ProgressiveBlock({
  ready,
  skeletonRows = 3,
  label,
  children,
}: ProgressiveBlockProps) {
  if (ready) {
    return (
      <div className="animate-reveal-up" data-testid={`progressive-block-${label}-ready`}>
        {children}
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={`${label}加载中`}
      data-testid={`progressive-block-${label}-loading`}
      style={{
        background: tokens.color.bg.secondary,
        border: `1px solid ${tokens.color.border.default}`,
        borderRadius: tokens.radius.md,
        padding: tokens.spacing.md,
      }}
    >
      <div
        className="animate-skeleton-shimmer"
        style={{
          height: 12,
          width: '38%',
          borderRadius: tokens.radius.sm,
          marginBottom: tokens.spacing.sm,
        }}
      />
      <Skeleton
        active
        title={false}
        paragraph={{ rows: skeletonRows, width: ['96%', '88%', '72%'].slice(0, skeletonRows) }}
      />
    </div>
  );
}
