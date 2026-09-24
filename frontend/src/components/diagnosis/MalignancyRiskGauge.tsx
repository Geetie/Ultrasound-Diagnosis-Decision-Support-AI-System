import { useState } from 'react';
import { Typography } from 'antd';
import { tokens } from '@/styles/tokens';
import type { MalignancyRisk, MalignancyRiskLevel } from '@/types/api';
import { MALIGNANCY_FEATURE_LABELS } from './displayConstants';

const { Text } = Typography;

/**
 * M5 恶性概率条。
 *
 * **必须与 AI 置信度分开展示**：置信度回答"模型对结论有多确信"，恶性概率
 * 回答"该病灶为恶性的可能性"。两者语义不同，混在一起展示会让医生把
 * "模型很确信这是 TR3"误读成"恶性概率高"。因此本组件固定使用
 * 红/黄/绿档位色，与置信度的蓝色条形成明确区分，并始终带文字档位标签
 * （不单靠颜色传意）。
 */
const LEVEL_META: Record<MalignancyRiskLevel, { label: string; color: string }> = {
  low: { label: '低', color: tokens.color.accent.success },
  medium: { label: '中', color: tokens.color.accent.warning },
  high: { label: '高', color: tokens.color.accent.danger },
};

interface MalignancyRiskGaugeProps {
  risk: MalignancyRisk | null | undefined;
}

export default function MalignancyRiskGauge({ risk }: MalignancyRiskGaugeProps) {
  const [expanded, setExpanded] = useState(false);

  if (!risk) {
    return null;
  }

  const meta = LEVEL_META[risk.level] ?? LEVEL_META.medium;
  const percent = Math.round(risk.probability * 100);
  const basis = risk.basis ?? [];

  return (
    <div
      data-testid="malignancy-risk-gauge"
      data-level={risk.level}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        minWidth: 190,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Text style={{ color: tokens.color.text.secondary, fontSize: 11 }}>恶性概率</Text>
        <Text
          data-testid="malignancy-risk-value"
          style={{ color: meta.color, fontSize: 13, fontWeight: 600 }}
        >
          {percent}%
        </Text>
        <Text
          data-testid="malignancy-risk-level"
          style={{
            color: meta.color,
            fontSize: 10,
            border: `1px solid ${meta.color}60`,
            borderRadius: tokens.radius.sm,
            padding: '0 4px',
            fontFamily: tokens.font.mono,
          }}
        >
          {meta.label}
        </Text>
        {basis.length > 0 && (
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
            {expanded ? '收起依据' : '查看依据'}
          </button>
        )}
      </div>

      <div
        style={{
          height: 6,
          borderRadius: 3,
          background: tokens.color.bg.hover,
          overflow: 'hidden',
        }}
      >
        <div
          data-testid="malignancy-risk-bar"
          style={{
            width: `${Math.max(0, Math.min(100, percent))}%`,
            height: '100%',
            background: meta.color,
            transition: 'width 400ms ease-out',
          }}
        />
      </div>

      {expanded && (
        <div
          data-testid="malignancy-risk-basis"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
            marginTop: 2,
            paddingLeft: 2,
            borderLeft: `2px solid ${meta.color}40`,
          }}
        >
          {basis.map((item) => (
            <Text
              key={item.feature}
              style={{ color: tokens.color.text.secondary, fontSize: 11 }}
            >
              {MALIGNANCY_FEATURE_LABELS[item.feature] ?? item.feature}
              <span
                style={{
                  color:
                    item.contribution >= 0
                      ? tokens.color.accent.danger
                      : tokens.color.accent.success,
                  marginLeft: 6,
                  fontFamily: tokens.font.mono,
                }}
              >
                {item.contribution >= 0 ? '+' : ''}
                {item.contribution.toFixed(2)}
              </span>
            </Text>
          ))}
          {risk.source_reference && (
            <Text style={{ color: tokens.color.text.muted, fontSize: 10 }}>
              依据：{risk.source_reference}
            </Text>
          )}
        </div>
      )}
    </div>
  );
}
