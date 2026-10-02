import { useState } from 'react';
import type { ImageQuality } from '@/types/api';
import {
  IMAGE_QUALITY_ISSUE_LABELS,
  resolveImageQualityLevel,
} from '@/types/navigation';
import { tokens } from '@/styles/tokens';

/**
 * M6 图像质量提示条：位于图像底部。
 *
 * 阈值口径（规格）：≥0.7 不提示 / 0.5-0.7 黄色警示 / <0.5 红色严重提示。
 * 提示条带文字说明（不单靠颜色），可展开查看模糊/伪影/增益分项。
 */
interface ImageQualityHintProps {
  quality: ImageQuality | null | undefined;
}

export default function ImageQualityHint({ quality }: ImageQualityHintProps) {
  const [expanded, setExpanded] = useState(false);
  const level = resolveImageQualityLevel(quality);

  if (!quality || level === 'ok') {
    return null;
  }

  const isSevere = level === 'severe';
  const color = isSevere ? tokens.color.accent.danger : tokens.color.accent.warning;
  const label = isSevere ? '图像质量差 — 可能影响判读' : '图像质量欠佳';
  const scorePercent = Math.round(quality.overall_score * 100);
  const issues = (quality.issues ?? []).map(
    (code) => IMAGE_QUALITY_ISSUE_LABELS[code] ?? code
  );
  const subScores: Array<[string, number | null | undefined]> = [
    ['清晰度', quality.blur_score],
    ['伪影', quality.artifact_score],
    ['增益', quality.gain_score],
  ];

  return (
    <div
      data-testid="image-quality-hint"
      data-level={level}
      style={{
        pointerEvents: 'auto',
        border: `1px solid ${color}`,
        borderRadius: tokens.radius.sm,
        background: 'rgba(5, 7, 15, 0.86)',
        color: tokens.color.text.primary,
        fontFamily: tokens.font.body,
        fontSize: 11,
        overflow: 'hidden',
      }}
    >
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          width: '100%',
          padding: '5px 10px',
          border: 'none',
          background: 'transparent',
          color: 'inherit',
          cursor: 'pointer',
          fontFamily: 'inherit',
          fontSize: 'inherit',
          textAlign: 'left',
        }}
      >
        <span
          aria-hidden="true"
          style={{
            display: 'inline-block',
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: color,
            flexShrink: 0,
          }}
        />
        <span style={{ color, fontWeight: 600 }}>{label}</span>
        <span style={{ color: tokens.color.text.secondary }}>
          质量 {scorePercent}%
        </span>
        {issues.length > 0 && (
          <span style={{ color: tokens.color.text.secondary }}>
            · {issues.join('、')}
          </span>
        )}
        <span style={{ marginLeft: 'auto', color: tokens.color.text.muted }}>
          {expanded ? '收起' : '分项'}
        </span>
      </button>

      {expanded && (
        <div
          data-testid="image-quality-detail"
          style={{
            padding: '6px 10px 8px',
            borderTop: `1px solid ${tokens.color.border.default}`,
            color: tokens.color.text.secondary,
            lineHeight: 1.7,
          }}
        >
          {subScores.map(([name, value]) => (
            <div key={name}>
              {name}：{value === null || value === undefined ? '—' : Math.round(value * 100) + '%'}
            </div>
          ))}
          {quality.diagnostic_impact && <div>判读影响：{quality.diagnostic_impact}</div>}
        </div>
      )}
    </div>
  );
}
