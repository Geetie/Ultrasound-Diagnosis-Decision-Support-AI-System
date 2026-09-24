import { useState } from 'react';
import { Tag, Typography } from 'antd';
import { tokens } from '@/styles/tokens';
import type { DifferentialDiagnosis } from '@/types/api';
import { confidenceColor } from './displayConstants';

const { Text } = Typography;

/**
 * M4 鉴别诊断清单。
 *
 * 展示口径（规格）：按可能性降序的有序卡片，置信度条绿 >0.7 / 黄 0.4-0.7 /
 * 红 <0.4。模型未给出可能性时显示"—"——**不反推数值**，否则会凭空制造出
 * 看似精确的概率。
 */
interface DifferentialDiagnosisListProps {
  items: DifferentialDiagnosis[] | null | undefined;
}

export default function DifferentialDiagnosisList({ items }: DifferentialDiagnosisListProps) {
  const [expandedRank, setExpandedRank] = useState<number | null>(null);

  if (!items || items.length === 0) {
    return null;
  }

  const ordered = [...items].sort((a, b) => a.rank - b.rank);

  return (
    <div
      data-testid="differential-diagnosis-list"
      style={{ display: 'flex', flexDirection: 'column', gap: tokens.spacing.sm }}
    >
      {ordered.map((item) => {
        const color = confidenceColor(item.confidence);
        const hasConfidence = item.confidence !== null && item.confidence !== undefined;
        const percent = hasConfidence ? Math.round((item.confidence as number) * 100) : null;
        const expanded = expandedRank === item.rank;
        const hasEvidence =
          item.supporting_evidence.length > 0 || item.excluding_evidence.length > 0;

        return (
          <div
            key={`${item.rank}-${item.disease_name}`}
            data-testid={`differential-item-${item.rank}`}
            style={{
              background: tokens.color.bg.elevated,
              border: `1px solid ${tokens.color.border.default}`,
              borderLeft: `3px solid ${color}`,
              borderRadius: tokens.radius.sm,
              padding: `${tokens.spacing.sm}px ${tokens.spacing.md}px`,
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.sm }}>
              <Text
                style={{
                  color: tokens.color.text.muted,
                  fontSize: 11,
                  fontFamily: tokens.font.mono,
                  minWidth: 18,
                }}
              >
                {item.rank}
              </Text>
              <Text style={{ color: tokens.color.text.primary, fontSize: 13, fontWeight: 600 }}>
                {item.disease_name}
              </Text>
              {item.disease_code && (
                <Tag
                  style={{
                    background: tokens.color.accent.primaryAlpha20,
                    border: `1px solid ${tokens.color.accent.primary}40`,
                    color: tokens.color.accent.primary,
                    fontFamily: tokens.font.mono,
                    fontSize: 10,
                    margin: 0,
                  }}
                >
                  {item.disease_code}
                </Tag>
              )}
              <Text
                data-testid={`differential-confidence-${item.rank}`}
                style={{
                  marginLeft: 'auto',
                  color,
                  fontSize: 12,
                  fontWeight: 600,
                  fontFamily: tokens.font.mono,
                }}
              >
                {percent === null ? '—' : `${percent}%`}
              </Text>
              {hasEvidence && (
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() => setExpandedRank(expanded ? null : item.rank)}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: tokens.color.text.muted,
                    fontSize: 10,
                    cursor: 'pointer',
                    padding: 0,
                    fontFamily: tokens.font.body,
                  }}
                >
                  {expanded ? '收起依据' : '依据'}
                </button>
              )}
            </div>

            {percent !== null && (
              <div
                style={{
                  height: 4,
                  borderRadius: 2,
                  background: tokens.color.bg.hover,
                  overflow: 'hidden',
                }}
              >
                <div
                  data-testid={`differential-bar-${item.rank}`}
                  style={{
                    width: `${Math.max(0, Math.min(100, percent))}%`,
                    height: '100%',
                    background: color,
                    transition: 'width 400ms ease-out',
                  }}
                />
              </div>
            )}

            {!hasConfidence && (
              <Text style={{ color: tokens.color.text.muted, fontSize: 10 }}>
                模型未给出该候选的可能性
              </Text>
            )}

            {expanded && hasEvidence && (
              <div
                data-testid={`differential-evidence-${item.rank}`}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 2,
                  marginTop: 2,
                  fontSize: 11,
                }}
              >
                {item.supporting_evidence.length > 0 && (
                  <Text style={{ color: tokens.color.accent.success, fontSize: 11 }}>
                    支持：{item.supporting_evidence.join('、')}
                  </Text>
                )}
                {item.excluding_evidence.length > 0 && (
                  <Text style={{ color: tokens.color.accent.danger, fontSize: 11 }}>
                    排除：{item.excluding_evidence.join('、')}
                  </Text>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
