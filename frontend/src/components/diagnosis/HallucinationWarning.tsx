import { Typography, Button } from 'antd';
import { WarningOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import { tokens } from '@/styles/tokens';

const { Text, Title } = Typography;

interface HallucinationWarningProps {
  visible: boolean;
  reviewReason: string | null;
  onAcknowledgeReview: () => void;
}

export default function HallucinationWarning({
  visible,
  reviewReason,
  onAcknowledgeReview,
}: HallucinationWarningProps) {
  if (!visible) return null;

  return (
    <div
      style={{
        background: 'linear-gradient(135deg, rgba(239, 68, 68, 0.15) 0%, rgba(239, 68, 68, 0.08) 100%)',
        border: `2px solid ${tokens.color.accent.danger}`,
        borderRadius: tokens.radius.md,
        padding: tokens.spacing.lg,
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: 3,
          background: tokens.color.accent.danger,
          animation: 'pulse 2s ease-in-out infinite',
        }}
      />
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: tokens.spacing.md }}>
        <ExclamationCircleOutlined
          style={{
            fontSize: 24,
            color: tokens.color.accent.danger,
            marginTop: 2,
            flexShrink: 0,
          }}
        />
        <div style={{ flex: 1 }}>
          <Title
            level={5}
            style={{
              color: tokens.color.accent.danger,
              fontFamily: tokens.font.display,
              fontSize: 14,
              letterSpacing: '0.05em',
              margin: 0,
              marginBottom: tokens.spacing.xs,
            }}
          >
            SEVERE HALLUCINATION DETECTED
          </Title>
          <Text
            style={{
              color: tokens.color.text.secondary,
              fontSize: 13,
              display: 'block',
              marginBottom: tokens.spacing.sm,
            }}
          >
            检测到严重幻觉输出，本诊断结论不可信赖，必须进行人工审核。
          </Text>
          {reviewReason && (
            <div
              style={{
                background: 'rgba(239, 68, 68, 0.1)',
                borderRadius: tokens.radius.sm,
                padding: `${tokens.spacing.sm}px ${tokens.spacing.md}px`,
                marginBottom: tokens.spacing.md,
                border: `1px solid rgba(239, 68, 68, 0.2)`,
              }}
            >
              <Text style={{ color: tokens.color.text.muted, fontSize: 11, fontFamily: tokens.font.mono }}>
                审核原因：
              </Text>
              <Text style={{ color: tokens.color.accent.danger, fontSize: 12, fontFamily: tokens.font.mono }}>
                {reviewReason}
              </Text>
            </div>
          )}
          <div style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.sm }}>
            <Button
              type="primary"
              danger
              icon={<WarningOutlined />}
              size="small"
              onClick={onAcknowledgeReview}
              style={{
                background: tokens.color.accent.danger,
                borderColor: tokens.color.accent.danger,
                fontFamily: tokens.font.body,
              }}
            >
              确认需人工审核
            </Button>
            <Text style={{ color: tokens.color.text.muted, fontSize: 11 }}>
              此警告不可关闭，必须确认审核
            </Text>
          </div>
        </div>
      </div>
    </div>
  );
}
