import { useState } from 'react';
import type { PlaneAssessment } from '@/types/api';
import {
  PLANE_ISSUE_LABELS,
  PLANE_TYPE_LABELS,
  resolvePlaneStatusLevel,
} from '@/types/navigation';
import { tokens } from '@/styles/tokens';

/**
 * M1 切面状态徽标：图像角落的切面合格性指示。
 *
 * 三态：绿勾（标准）/ 黄警（非标准）/ 灰问（无数据）。**始终带文字标签**，
 * 不单靠颜色传意（无障碍要求）。点击可展开切面类型、问题清单与可见结构。
 */
const LEVEL_STYLE = {
  standard: { color: tokens.color.accent.success, icon: '✓', label: '切面标准' },
  warning: { color: tokens.color.accent.warning, icon: '!', label: '切面非标准' },
  unknown: { color: tokens.color.text.muted, icon: '?', label: '切面无数据' },
} as const;

interface PlaneStatusBadgeProps {
  assessment: PlaneAssessment | null | undefined;
}

export default function PlaneStatusBadge({ assessment }: PlaneStatusBadgeProps) {
  const [expanded, setExpanded] = useState(false);
  const level = resolvePlaneStatusLevel(assessment);
  const style = LEVEL_STYLE[level];
  const issues = (assessment?.issues ?? []).map(
    (code) => PLANE_ISSUE_LABELS[code] ?? code
  );
  const scorePercent = assessment ? Math.round(assessment.plane_score * 100) : null;
  const structures = assessment?.required_structures_visible ?? [];

  return (
    <div
      data-testid="plane-status-badge"
      data-level={level}
      style={{ pointerEvents: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}
    >
      <button
        type="button"
        aria-expanded={expanded}
        aria-label={`切面状态：${style.label}`}
        onClick={() => setExpanded((value) => !value)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '4px 10px',
          borderRadius: tokens.radius.sm,
          border: `1px solid ${style.color}`,
          background: 'rgba(10, 14, 26, 0.72)',
          color: style.color,
          cursor: 'pointer',
          fontFamily: tokens.font.body,
          fontSize: 11,
          fontWeight: 600,
        }}
      >
        <span
          aria-hidden="true"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 14,
            height: 14,
            borderRadius: '50%',
            background: style.color,
            color: tokens.color.bg.primary,
            fontSize: 10,
            fontWeight: 700,
          }}
        >
          {style.icon}
        </span>
        <span>{style.label}</span>
        {scorePercent !== null && (
          <span style={{ color: tokens.color.text.secondary, fontWeight: 400 }}>
            {scorePercent}%
          </span>
        )}
      </button>

      {expanded && (
        <div
          data-testid="plane-status-detail"
          style={{
            minWidth: 190,
            padding: '8px 10px',
            borderRadius: tokens.radius.sm,
            border: `1px solid ${tokens.color.border.default}`,
            background: 'rgba(5, 7, 15, 0.92)',
            color: tokens.color.text.primary,
            fontFamily: tokens.font.body,
            fontSize: 11,
            lineHeight: 1.6,
          }}
        >
          <div>
            切面类型：
            {assessment?.plane_type
              ? PLANE_TYPE_LABELS[assessment.plane_type] ?? assessment.plane_type
              : '—'}
          </div>
          {assessment?.organ_code && <div>器官：{assessment.organ_code}</div>}
          <div>问题：{issues.length > 0 ? issues.join('、') : '未见'}</div>
          <div>可见结构：{structures.length > 0 ? structures.join('、') : '—'}</div>
        </div>
      )}
    </div>
  );
}
