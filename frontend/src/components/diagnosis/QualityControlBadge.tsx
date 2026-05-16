import { Tag } from 'antd';
import { tokens } from '@/styles/tokens';

type HallucinationSeverity = 'severe' | 'moderate' | 'mild' | null;
type FusionStatus = 'normal' | 'bypassed';
type DesensitizationStatus = 'full' | 'degraded';

interface QualityControlBadgeProps {
  hallucination_severity: HallucinationSeverity;
  fusion_status: FusionStatus;
  desensitization_status: DesensitizationStatus;
}

const severityConfig: Record<string, { color: string; label: string; bg: string }> = {
  severe: { color: tokens.color.accent.danger, label: '严重幻觉', bg: 'rgba(239, 68, 68, 0.15)' },
  moderate: { color: tokens.color.accent.warning, label: '中度幻觉', bg: 'rgba(245, 158, 11, 0.15)' },
  mild: { color: tokens.color.accent.info, label: '轻度幻觉', bg: 'rgba(59, 130, 246, 0.15)' },
};

const fusionConfig: Record<string, { color: string; label: string; bg: string }> = {
  normal: { color: tokens.color.accent.success, label: '融合正常', bg: 'rgba(16, 185, 129, 0.15)' },
  bypassed: { color: tokens.color.accent.warning, label: '融合旁路', bg: 'rgba(245, 158, 11, 0.15)' },
};

const desensitizationConfig: Record<string, { color: string; label: string; bg: string }> = {
  full: { color: tokens.color.accent.success, label: '完整脱敏', bg: 'rgba(16, 185, 129, 0.15)' },
  degraded: { color: tokens.color.accent.danger, label: '脱敏降级', bg: 'rgba(239, 68, 68, 0.15)' },
};

export default function QualityControlBadge({
  hallucination_severity,
  fusion_status,
  desensitization_status,
}: QualityControlBadgeProps) {
  const renderBadge = (
    config: Record<string, { color: string; label: string; bg: string }>,
    key: string | null | undefined,
  ) => {
    if (!key || !config[key]) return null;
    const item = config[key];
    return (
      <Tag
        style={{
          color: item.color,
          background: item.bg,
          border: `1px solid ${item.color}40`,
          fontFamily: tokens.font.mono,
          fontSize: 11,
          margin: 0,
        }}
      >
        {item.label}
      </Tag>
    );
  };

  return (
    <div style={{ display: 'flex', gap: tokens.spacing.sm, flexWrap: 'wrap' }}>
      {renderBadge(severityConfig, hallucination_severity)}
      {renderBadge(fusionConfig, fusion_status)}
      {renderBadge(desensitizationConfig, desensitization_status)}
    </div>
  );
}
