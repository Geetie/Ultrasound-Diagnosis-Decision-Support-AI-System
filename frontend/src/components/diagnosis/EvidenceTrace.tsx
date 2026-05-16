import { Collapse, Typography, Tag } from 'antd';
import {
  BranchesOutlined,
  BookOutlined,
  SafetyCertificateOutlined,
  CloudOutlined,
} from '@ant-design/icons';
import { tokens } from '@/styles/tokens';
import { useUiStore } from '@/stores/uiStore';

const { Text } = Typography;

interface ReasoningStep {
  step: string;
  description: string;
  confidence?: number;
}

interface KnowledgeRef {
  source: string;
  version: string;
  relevance: number;
}

interface EvidenceData {
  reasoning_path?: ReasoningStep[];
  knowledge_refs?: KnowledgeRef[];
  confidence_score?: number;
  cached?: boolean;
}

interface EvidenceTraceProps {
  evidence: EvidenceData;
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    pct >= 80
      ? tokens.color.accent.success
      : pct >= 50
        ? tokens.color.accent.warning
        : tokens.color.accent.danger;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.sm }}>
      <div
        style={{
          flex: 1,
          height: 6,
          background: tokens.color.bg.hover,
          borderRadius: 3,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: '100%',
            background: color,
            borderRadius: 3,
            transition: 'width 0.6s ease',
          }}
        />
      </div>
      <Text style={{ color, fontFamily: tokens.font.mono, fontSize: 12, minWidth: 40, textAlign: 'right' }}>
        {pct}%
      </Text>
    </div>
  );
}

function ReasoningPathItem({ step, index }: { step: ReasoningStep; index: number }) {
  const isDarkRoom = useUiStore((s) => s.isDarkRoomMode);
  const isCached = step.confidence !== undefined && step.confidence < 0.6;

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: tokens.spacing.sm,
        padding: `${tokens.spacing.sm}px 0`,
        background: isCached
          ? isDarkRoom
            ? tokens.color.darkroom.amberBg
            : 'rgba(245, 158, 11, 0.08)'
          : 'transparent',
        borderRadius: tokens.radius.sm,
        paddingLeft: tokens.spacing.sm,
        paddingRight: tokens.spacing.sm,
        borderLeft: isCached
          ? isDarkRoom
            ? `3px solid ${tokens.color.darkroom.amberBorder}`
            : `3px solid ${tokens.color.accent.warning}40`
          : '3px solid transparent',
      }}
    >
      <div
        style={{
          width: 22,
          height: 22,
          borderRadius: '50%',
          background: tokens.color.accent.primaryAlpha20,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
          marginTop: 1,
        }}
      >
        <Text style={{ color: tokens.color.accent.primary, fontSize: 11, fontFamily: tokens.font.mono }}>
          {index + 1}
        </Text>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Text style={{ color: tokens.color.text.primary, fontSize: 13, display: 'block' }}>
          {step.step}
        </Text>
        <Text style={{ color: tokens.color.text.muted, fontSize: 11, display: 'block', marginTop: 2 }}>
          {step.description}
        </Text>
        {step.confidence !== undefined && (
          <div style={{ marginTop: 6 }}>
            <ConfidenceBar value={step.confidence} />
          </div>
        )}
        {isCached && (
          <Tag
            style={{
              color: isDarkRoom ? tokens.color.darkroom.amber : tokens.color.accent.warning,
              background: isDarkRoom ? 'rgba(245, 158, 11, 0.15)' : 'rgba(245, 158, 11, 0.1)',
              border: `1px solid ${isDarkRoom ? tokens.color.darkroom.amberBorder : 'rgba(245, 158, 11, 0.3)'}`,
              fontSize: 10,
              marginTop: 4,
              fontFamily: tokens.font.mono,
            }}
          >
            <CloudOutlined /> 缓存证据
          </Tag>
        )}
      </div>
    </div>
  );
}

export default function EvidenceTrace({ evidence }: EvidenceTraceProps) {
  const isDarkRoom = useUiStore((s) => s.isDarkRoomMode);
  const reasoningPath = evidence.reasoning_path ?? [];
  const knowledgeRefs = evidence.knowledge_refs ?? [];
  const overallConfidence = evidence.confidence_score;
  const isCached = evidence.cached ?? false;

  const panelStyle: React.CSSProperties = {
    background: tokens.color.bg.elevated,
    border: `1px solid ${tokens.color.border.default}`,
    borderRadius: tokens.radius.sm,
  };

  const headerStyle: React.CSSProperties = {
    color: tokens.color.text.primary,
    fontFamily: tokens.font.body,
    fontSize: 13,
  };

  return (
    <div
      style={{
        background: tokens.color.bg.secondary,
        border: `1px solid ${tokens.color.border.default}`,
        borderRadius: tokens.radius.md,
        padding: tokens.spacing.md,
        borderLeft: isCached
          ? isDarkRoom
            ? `3px solid ${tokens.color.darkroom.amberBorder}`
            : `3px solid ${tokens.color.accent.warning}`
          : `3px solid ${tokens.color.accent.primary}30`,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: tokens.spacing.md }}>
        <Text
          style={{
            fontFamily: tokens.font.display,
            color: tokens.color.accent.primary,
            fontSize: 12,
            letterSpacing: '0.05em',
          }}
        >
          EVIDENCE TRACE
        </Text>
        {overallConfidence !== undefined && (
          <div style={{ width: 120 }}>
            <ConfidenceBar value={overallConfidence} />
          </div>
        )}
      </div>

      <Collapse
        ghost
        expandIconPosition="start"
        style={{ background: 'transparent' }}
        items={[
          {
            key: 'reasoning',
            label: (
              <span style={headerStyle}>
                <BranchesOutlined style={{ marginRight: 6, color: tokens.color.accent.primary }} />
                推理路径 ({reasoningPath.length})
              </span>
            ),
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {reasoningPath.length > 0 ? (
                  reasoningPath.map((step, i) => (
                    <ReasoningPathItem key={i} step={step} index={i} />
                  ))
                ) : (
                  <Text style={{ color: tokens.color.text.muted, fontSize: 12 }}>暂无推理路径数据</Text>
                )}
              </div>
            ),
            style: panelStyle,
          },
          {
            key: 'knowledge',
            label: (
              <span style={headerStyle}>
                <BookOutlined style={{ marginRight: 6, color: tokens.color.accent.secondary }} />
                知识库引用 ({knowledgeRefs.length})
              </span>
            ),
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: tokens.spacing.xs }}>
                {knowledgeRefs.length > 0 ? (
                  knowledgeRefs.map((ref, i) => (
                    <div
                      key={i}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: `${tokens.spacing.xs}px ${tokens.spacing.sm}px`,
                        background: tokens.color.bg.hover,
                        borderRadius: tokens.radius.sm,
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.sm }}>
                        <SafetyCertificateOutlined style={{ color: tokens.color.accent.secondary, fontSize: 12 }} />
                        <Text style={{ color: tokens.color.text.primary, fontSize: 12 }}>{ref.source}</Text>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.sm }}>
                        <Text style={{ color: tokens.color.text.muted, fontSize: 10, fontFamily: tokens.font.mono }}>
                          v{ref.version}
                        </Text>
                        <ConfidenceBar value={ref.relevance} />
                      </div>
                    </div>
                  ))
                ) : (
                  <Text style={{ color: tokens.color.text.muted, fontSize: 12 }}>暂无知识库引用</Text>
                )}
              </div>
            ),
            style: panelStyle,
          },
        ]}
      />
    </div>
  );
}
