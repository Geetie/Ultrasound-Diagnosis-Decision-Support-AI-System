import { Card, Tag, Typography } from 'antd';
import { SafetyCertificateOutlined, ExperimentOutlined, EyeOutlined } from '@ant-design/icons';
import { tokens } from '@/styles/tokens';
import type { DiagnosisResult } from '@/types/api';
import MalignancyRiskGauge from './MalignancyRiskGauge';

const { Text, Title } = Typography;

type SafetyLevel = 'A' | 'B' | 'C';

interface DiagnosisConclusionProps {
  result: DiagnosisResult;
  responseTier: 'realtime' | 'standard' | 'full';
}

const safetyLevelConfig: Record<SafetyLevel, { label: string; color: string; icon: React.ReactNode; desc: string }> = {
  A: {
    label: 'A级·诊断级',
    color: tokens.color.accent.success,
    icon: <SafetyCertificateOutlined />,
    desc: '可直接用于诊断决策',
  },
  B: {
    label: 'B级·导航级',
    color: tokens.color.accent.primary,
    icon: <ExperimentOutlined />,
    desc: '辅助导航定位，需医师确认',
  },
  C: {
    label: 'C级·观察级',
    color: tokens.color.accent.warning,
    icon: <EyeOutlined />,
    desc: '仅供参考观察，不可作为诊断依据',
  },
};

function getSafetyLevel(result: DiagnosisResult): SafetyLevel {
  if (result.hallucination_severity === 'severe') return 'C';
  if (result.hallucination_severity === 'moderate') return 'C';
  if (result.fusion_status === 'bypassed' || result.desensitization_status === 'degraded') return 'B';
  if (result.consistency_flag === 'inconsistent') return 'B';
  return 'A';
}

interface ConclusionSection {
  title: string;
  content: string;
  border: string;
  label: string;
  labelColor: string;
  isDiff?: boolean;
}

function parseConclusionSections(
  result: DiagnosisResult,
  tier: 'realtime' | 'standard' | 'full',
): ConclusionSection[] {
  const diagnosis = result.diagnosis as Record<string, unknown>;
  const sections: ConclusionSection[] = [];

  const standardConclusion = (diagnosis?.standard_conclusion ?? diagnosis?.conclusion ?? '') as string;
  const fullConclusion = (diagnosis?.full_conclusion ?? diagnosis?.deep_analysis ?? '') as string;

  if (tier === 'full' && fullConclusion) {
    sections.push({
      title: '初步结论',
      content: standardConclusion || '标准路径结论待生成',
      border: `2px solid ${tokens.color.accent.info}`,
      label: '初步结论',
      labelColor: tokens.color.accent.info,
    });

    const standardItems = new Set(
      standardConclusion.split(/[。；\n]/).filter((s: string) => s.trim()),
    );
    const fullItems = fullConclusion.split(/[。；\n]/).filter((s: string) => s.trim());

    const diffContent = fullItems
      .map((item: string) => {
        const isDiff = !standardItems.has(item);
        return { text: item, isDiff };
      });

    sections.push({
      title: '深度分析',
      content: '',
      border: `2px solid ${tokens.color.accent.success}`,
      label: '深度分析',
      labelColor: tokens.color.accent.success,
      isDiff: true,
      ...({ diffItems: diffContent } as any),
    });
  } else {
    sections.push({
      title: '诊断结论',
      content: standardConclusion || '暂无诊断结论',
      border: `1px solid ${tokens.color.border.default}`,
      label: '诊断结论',
      labelColor: tokens.color.accent.primary,
    });
  }

  return sections;
}

export default function DiagnosisConclusion({ result, responseTier }: DiagnosisConclusionProps) {
  const isSevere = result.hallucination_severity === 'severe';
  const safetyLevel = getSafetyLevel(result);
  const levelConfig = safetyLevelConfig[safetyLevel];
  const sections = parseConclusionSections(result, responseTier);

  const diagnosis = result.diagnosis as Record<string, unknown>;
  const organLabel = (diagnosis?.target_organ ?? '') as string;
  const findingSummary = (diagnosis?.finding_summary ?? diagnosis?.summary ?? '') as string;

  // M5：AI 置信度（蓝）与恶性概率（红/黄/绿）**并列但独立**展示。
  // 两者语义不同，合并成一条会让医生把"模型很确信"误读为"恶性概率高"。
  const quality = (result.quality_control ?? {}) as Record<string, unknown>;
  const confidence = typeof quality.confidence_score === 'number' ? quality.confidence_score : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: tokens.spacing.md }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.sm }}>
          <Title
            level={5}
            style={{
              fontFamily: tokens.font.display,
              color: tokens.color.accent.secondary,
              fontSize: 12,
              letterSpacing: '0.05em',
              margin: 0,
            }}
          >
            DIAGNOSIS CONCLUSION
          </Title>
          {organLabel && (
            <Tag
              style={{
                background: tokens.color.accent.primaryAlpha20,
                border: `1px solid ${tokens.color.accent.primary}40`,
                color: tokens.color.accent.primary,
                fontFamily: tokens.font.mono,
                fontSize: 11,
              }}
            >
              {organLabel}
            </Tag>
          )}
        </div>
        <Tag
          icon={levelConfig.icon}
          style={{
            background: `${levelConfig.color}15`,
            border: `1px solid ${levelConfig.color}40`,
            color: levelConfig.color,
            fontFamily: tokens.font.mono,
            fontSize: 12,
            fontWeight: 600,
            padding: '2px 10px',
          }}
        >
          {levelConfig.label}
        </Tag>
      </div>

      <Text style={{ color: tokens.color.text.muted, fontSize: 11 }}>{levelConfig.desc}</Text>

      {(confidence !== null || result.malignancy_risk) && (
        <div
          data-testid="probability-row"
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            flexWrap: 'wrap',
            gap: tokens.spacing.xl,
          }}
        >
          {confidence !== null && (
            <div
              data-testid="ai-confidence-gauge"
              style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 190 }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Text style={{ color: tokens.color.text.secondary, fontSize: 11 }}>
                  AI 置信度
                </Text>
                <Text
                  style={{
                    color: tokens.color.accent.primary,
                    fontSize: 13,
                    fontWeight: 600,
                  }}
                >
                  {Math.round(confidence * 100)}%
                </Text>
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
                  style={{
                    width: `${Math.max(0, Math.min(100, Math.round(confidence * 100)))}%`,
                    height: '100%',
                    background: tokens.color.accent.primary,
                    transition: 'width 400ms ease-out',
                  }}
                />
              </div>
            </div>
          )}

          <MalignancyRiskGauge risk={result.malignancy_risk} />
        </div>
      )}

      {findingSummary && (
        <div
          style={{
            background: tokens.color.bg.elevated,
            borderRadius: tokens.radius.sm,
            padding: `${tokens.spacing.sm}px ${tokens.spacing.md}px`,
            border: `1px solid ${tokens.color.border.default}`,
          }}
        >
          <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>{findingSummary}</Text>
        </div>
      )}

      {sections.map((section, idx) => (
        <Card
          key={idx}
          size="small"
          style={{
            background: tokens.color.bg.elevated,
            border: isSevere
              ? `2px solid ${tokens.color.accent.danger}`
              : section.border,
            borderRadius: tokens.radius.md,
            boxShadow: isSevere
              ? `0 0 12px ${tokens.color.accent.danger}30`
              : 'none',
          }}
          styles={{ body: { padding: tokens.spacing.md } }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.sm, marginBottom: tokens.spacing.sm }}>
            <Tag
              style={{
                background: `${section.labelColor}15`,
                border: `1px solid ${section.labelColor}40`,
                color: section.labelColor,
                fontFamily: tokens.font.mono,
                fontSize: 11,
                margin: 0,
              }}
            >
              {section.label}
            </Tag>
          </div>

          {section.isDiff && (section as any).diffItems ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {(section as any).diffItems.map(
                (item: { text: string; isDiff: boolean }, i: number) => (
                  <div
                    key={i}
                    style={{
                      padding: `${tokens.spacing.xs}px ${tokens.spacing.sm}px`,
                      borderRadius: tokens.radius.sm,
                      background: item.isDiff ? 'rgba(245, 158, 11, 0.1)' : 'transparent',
                      borderLeft: item.isDiff
                        ? `3px solid ${tokens.color.accent.warning}`
                        : '3px solid transparent',
                    }}
                  >
                    <Text
                      style={{
                        color: item.isDiff ? tokens.color.accent.warning : tokens.color.text.primary,
                        fontSize: 13,
                        fontWeight: item.isDiff ? 500 : 400,
                      }}
                    >
                      {item.text}
                    </Text>
                    {item.isDiff && (
                      <Tag
                        style={{
                          marginLeft: 8,
                          color: tokens.color.accent.warning,
                          background: 'rgba(245, 158, 11, 0.15)',
                          border: `1px solid rgba(245, 158, 11, 0.3)`,
                          fontSize: 10,
                          fontFamily: tokens.font.mono,
                        }}
                      >
                        差异
                      </Tag>
                    )}
                  </div>
                ),
              )}
            </div>
          ) : (
            <Text style={{ color: tokens.color.text.primary, fontSize: 13, lineHeight: 1.8 }}>
              {section.content}
            </Text>
          )}
        </Card>
      ))}

      {isSevere && (
        <div
          style={{
            background: 'rgba(239, 68, 68, 0.08)',
            borderRadius: tokens.radius.sm,
            padding: `${tokens.spacing.sm}px ${tokens.spacing.md}px`,
            border: `1px dashed ${tokens.color.accent.danger}60`,
            display: 'flex',
            alignItems: 'center',
            gap: tokens.spacing.sm,
          }}
        >
          <SafetyCertificateOutlined style={{ color: tokens.color.accent.danger, fontSize: 14 }} />
          <Text style={{ color: tokens.color.accent.danger, fontSize: 12, fontFamily: tokens.font.mono }}>
            强制人工审核 — 结论因严重幻觉已被标记为不可信赖
          </Text>
        </div>
      )}
    </div>
  );
}
