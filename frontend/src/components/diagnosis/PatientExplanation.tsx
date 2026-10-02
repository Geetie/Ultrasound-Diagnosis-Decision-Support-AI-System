import { useState } from 'react';
import { Tag, Typography } from 'antd';
import { tokens } from '@/styles/tokens';
import type { PatientExplanation as PatientExplanationData } from '@/types/api';

const { Text, Paragraph } = Typography;

/**
 * M10 患者解释话术。
 *
 * 设计要点：
 * 1. **默认折叠**且显式标注「非正式报告内容」——它是给患者听的，不是医疗
 *    文书的一部分；不标注就容易在打印/截图时被当成诊断意见；
 * 2. 提供「患者版视图」切换：打开后隐藏医生向的术语标签、放大字号，医生可
 *    直接对着屏幕向患者说明；
 * 3. 无话术时整块不渲染（后端未产出话术时返回 null，这里不编造安抚话术）。
 */
interface PatientExplanationProps {
  explanation: PatientExplanationData | null | undefined;
}

export default function PatientExplanation({ explanation }: PatientExplanationProps) {
  const [expanded, setExpanded] = useState(false);
  const [patientView, setPatientView] = useState(false);

  if (!explanation || !explanation.explanation) {
    return null;
  }

  const rows: Array<{ label: string; value: string }> = [
    { label: '解释', value: explanation.explanation },
  ];
  if (explanation.reassurance) {
    rows.push({ label: '安抚', value: explanation.reassurance });
  }
  if (explanation.next_step) {
    rows.push({ label: '下一步', value: explanation.next_step });
  }

  return (
    <div
      data-testid="patient-explanation"
      style={{
        background: tokens.color.bg.secondary,
        border: `1px solid ${tokens.color.border.default}`,
        borderLeft: `3px solid ${tokens.color.accent.info}40`,
        borderRadius: tokens.radius.md,
        padding: `${tokens.spacing.sm}px ${tokens.spacing.md}px`,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.sm, flexWrap: 'wrap' }}>
        <Text
          style={{
            fontFamily: tokens.font.display,
            color: tokens.color.accent.info,
            fontSize: 12,
            letterSpacing: '0.05em',
          }}
        >
          PATIENT EXPLANATION
        </Text>
        <Tag
          data-testid="patient-explanation-disclaimer"
          style={{
            margin: 0,
            background: `${tokens.color.accent.warning}15`,
            border: `1px solid ${tokens.color.accent.warning}40`,
            color: tokens.color.accent.warning,
            fontSize: 10,
          }}
        >
          非正式报告内容
        </Tag>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: tokens.spacing.sm }}>
          {expanded && (
            <button
              type="button"
              data-testid="patient-explanation-view-toggle"
              onClick={() => setPatientView((value) => !value)}
              style={{
                background: 'transparent',
                border: `1px solid ${tokens.color.border.default}`,
                borderRadius: tokens.radius.sm,
                color: tokens.color.text.secondary,
                fontSize: 10,
                cursor: 'pointer',
                padding: '2px 8px',
                fontFamily: tokens.font.body,
              }}
            >
              {patientView ? '医生视图' : '患者版视图'}
            </button>
          )}
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
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
            {expanded ? '收起' : '展开'}
          </button>
        </div>
      </div>

      {expanded && (
        <div
          data-testid="patient-explanation-body"
          data-view={patientView ? 'patient' : 'doctor'}
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: tokens.spacing.sm,
            marginTop: tokens.spacing.sm,
          }}
        >
          {rows.map((row) => (
            <div key={row.label} style={{ display: 'flex', gap: tokens.spacing.sm }}>
              {!patientView && (
                <Text
                  style={{
                    color: tokens.color.text.muted,
                    fontSize: 11,
                    minWidth: 36,
                    flexShrink: 0,
                  }}
                >
                  {row.label}
                </Text>
              )}
              <Paragraph
                data-testid={`patient-explanation-${row.label}`}
                style={{
                  margin: 0,
                  color: tokens.color.text.primary,
                  fontSize: patientView ? 15 : 13,
                  lineHeight: 1.6,
                }}
              >
                {row.value}
              </Paragraph>
            </div>
          ))}

          {explanation.source_reference && (
            <Text
              data-testid="patient-explanation-source"
              style={{ color: tokens.color.text.muted, fontSize: 10 }}
            >
              {explanation.source_reference}
            </Text>
          )}
        </div>
      )}
    </div>
  );
}
