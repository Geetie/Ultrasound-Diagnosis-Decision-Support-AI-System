import { useState } from 'react';
import { Typography } from 'antd';
import { tokens } from '@/styles/tokens';
import type { ResearchMetrics as ResearchMetricsData } from '@/types/api';

const { Text } = Typography;

/**
 * M11 科研量化指标。
 *
 * 设计要点：
 * 1. **默认折叠**：这些指标服务科研，不是临床决策依据，展开会稀释结论；
 * 2. **来源标注**：置信度分布是由恶性概率与 AI 置信度派生的视图，不是模型
 *    输出的真实分布——界面上必须写明，否则会被当成模型内部概率；
 * 3. 逐项缺失即不显示：没有弹性成像就看不到弹性评分（后端不留空数值）。
 */

const DISTRIBUTION_SEGMENTS: Array<{ key: 'benign' | 'malignant' | 'indeterminate'; label: string; color: string }> = [
  { key: 'benign', label: '良性', color: tokens.color.accent.success },
  { key: 'malignant', label: '恶性', color: tokens.color.accent.danger },
  { key: 'indeterminate', label: '不确定', color: tokens.color.accent.warning },
];

interface ResearchMetricsProps {
  metrics: ResearchMetricsData | null | undefined;
  /** 调用方可指定初始展开（例如科研模式），默认折叠 */
  defaultExpanded?: boolean;
}

export default function ResearchMetrics({ metrics, defaultExpanded = false }: ResearchMetricsProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  if (!metrics) {
    return null;
  }

  const bloodFlow = metrics.blood_flow;
  const distribution = metrics.confidence_distribution;
  const hasHash = Boolean(metrics.ai_features?.feature_vector_hash);
  const hasBloodFlow = Boolean(
    bloodFlow &&
      ((bloodFlow.psv ?? null) !== null ||
        (bloodFlow.edv ?? null) !== null ||
        (bloodFlow.ri ?? null) !== null)
  );

  const rows: Array<{ label: string; value: string }> = [];
  if (metrics.elasticity_score !== null && metrics.elasticity_score !== undefined) {
    rows.push({
      label: '弹性评分',
      value: `${metrics.elasticity_score}${metrics.elasticity_unit ? ` ${metrics.elasticity_unit}` : ''}`,
    });
  }
  if (hasBloodFlow && bloodFlow) {
    const parts: string[] = [];
    if (bloodFlow.psv !== null && bloodFlow.psv !== undefined) parts.push(`PSV ${bloodFlow.psv}`);
    if (bloodFlow.edv !== null && bloodFlow.edv !== undefined) parts.push(`EDV ${bloodFlow.edv}`);
    if (bloodFlow.ri !== null && bloodFlow.ri !== undefined) parts.push(`RI ${bloodFlow.ri}`);
    rows.push({ label: '血流参数', value: `${parts.join(' / ')} ${bloodFlow.unit}` });
  }
  if (hasHash) {
    rows.push({ label: '特征指纹', value: String(metrics.ai_features?.feature_vector_hash) });
  }

  return (
    <div
      data-testid="research-metrics"
      style={{
        background: tokens.color.bg.secondary,
        border: `1px solid ${tokens.color.border.default}`,
        borderLeft: `3px solid ${tokens.color.accent.secondary}40`,
        borderRadius: tokens.radius.md,
        padding: `${tokens.spacing.sm}px ${tokens.spacing.md}px`,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.sm }}>
        <Text
          style={{
            fontFamily: tokens.font.display,
            color: tokens.color.accent.secondary,
            fontSize: 12,
            letterSpacing: '0.05em',
          }}
        >
          RESEARCH METRICS
        </Text>
        <Text style={{ color: tokens.color.text.muted, fontSize: 10 }}>科研用途，非临床决策依据</Text>
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
          style={{
            marginLeft: 'auto',
            background: 'transparent',
            border: 'none',
            color: tokens.color.text.muted,
            fontSize: 10,
            cursor: 'pointer',
            padding: 0,
            fontFamily: tokens.font.body,
          }}
        >
          {expanded ? '收起' : '展开'}
        </button>
      </div>

      {expanded && (
        <div
          data-testid="research-metrics-body"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: tokens.spacing.sm,
            marginTop: tokens.spacing.sm,
          }}
        >
          {rows.length > 0 ? (
            rows.map((row) => (
              <div key={row.label} style={{ display: 'flex', gap: tokens.spacing.sm }}>
                <Text style={{ color: tokens.color.text.muted, fontSize: 11, minWidth: 60, flexShrink: 0 }}>
                  {row.label}
                </Text>
                <Text
                  style={{
                    color: tokens.color.text.primary,
                    fontSize: 12,
                    fontFamily: tokens.font.mono,
                  }}
                >
                  {row.value}
                </Text>
              </div>
            ))
          ) : (
            <Text style={{ color: tokens.color.text.muted, fontSize: 11 }}>
              本次未提取到弹性/血流参数（需弹性成像或频谱多普勒数据）
            </Text>
          )}

          {distribution && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <Text style={{ color: tokens.color.text.muted, fontSize: 11 }}>置信度分布</Text>
              <div
                data-testid="confidence-distribution"
                style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden' }}
              >
                {DISTRIBUTION_SEGMENTS.map((segment) => {
                  const value = distribution[segment.key] ?? 0;
                  return (
                    <div
                      key={segment.key}
                      data-testid={`distribution-${segment.key}`}
                      style={{
                        width: `${Math.max(0, Math.min(100, value * 100))}%`,
                        background: segment.color,
                      }}
                    />
                  );
                })}
              </div>
              <div style={{ display: 'flex', gap: tokens.spacing.md, flexWrap: 'wrap' }}>
                {DISTRIBUTION_SEGMENTS.map((segment) => (
                  <Text
                    key={segment.key}
                    data-testid={`distribution-label-${segment.key}`}
                    style={{ color: segment.color, fontSize: 10, fontFamily: tokens.font.mono }}
                  >
                    {segment.label} {Math.round((distribution[segment.key] ?? 0) * 100)}%
                  </Text>
                ))}
              </div>
              <Text style={{ color: tokens.color.text.muted, fontSize: 10 }}>
                由恶性概率与 AI 置信度派生，非模型输出的原始分布
              </Text>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
