import { Tag, Typography } from 'antd';
import { tokens } from '@/styles/tokens';
import type { ClinicalAction, ClinicalActionUrgency } from '@/types/api';

const { Text } = Typography;

/**
 * M7 转诊/处置建议。
 *
 * 设计要点：
 * 1. **按紧急度着色与排序**：处置建议的价值在于"先做什么"，因此 urgent 排最前；
 * 2. **适用层级显性化**：基层医院做不了穿刺，把"三甲"标出来，避免被误读为
 *    "必须马上转诊"或"本院就能做"；
 * 3. 不显示"无需处置"——本组件只在有处置建议时渲染（后端未产出建议时整块不出现）。
 */
const URGENCY_META: Record<ClinicalActionUrgency, { label: string; color: string; order: number }> = {
  urgent: { label: '紧急', color: tokens.color.accent.danger, order: 0 },
  soon: { label: '尽快', color: tokens.color.accent.warning, order: 1 },
  routine: { label: '常规', color: tokens.color.accent.info, order: 2 },
};

const LEVEL_COLORS: Record<string, string> = {
  三甲: tokens.color.accent.secondary,
  基层: tokens.color.accent.primary,
};

interface ClinicalActionsProps {
  actions: ClinicalAction[] | null | undefined;
}

export default function ClinicalActions({ actions }: ClinicalActionsProps) {
  if (!actions || actions.length === 0) {
    return null;
  }

  const ordered = [...actions].sort(
    (a, b) => (URGENCY_META[a.urgency]?.order ?? 9) - (URGENCY_META[b.urgency]?.order ?? 9)
  );

  return (
    <div
      data-testid="clinical-actions"
      style={{ display: 'flex', flexDirection: 'column', gap: tokens.spacing.sm }}
    >
      {ordered.map((action, index) => {
        const meta = URGENCY_META[action.urgency] ?? URGENCY_META.routine;
        const levelColor = action.applicable_level
          ? LEVEL_COLORS[action.applicable_level] ?? tokens.color.text.muted
          : null;
        return (
          <div
            key={`${action.action_type}-${index}`}
            data-testid={`clinical-action-${index}`}
            data-urgency={action.urgency}
            style={{
              background: tokens.color.bg.elevated,
              border: `1px solid ${tokens.color.border.default}`,
              borderLeft: `3px solid ${meta.color}`,
              borderRadius: tokens.radius.sm,
              padding: `${tokens.spacing.sm}px ${tokens.spacing.md}px`,
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.sm, flexWrap: 'wrap' }}>
              <Tag
                data-testid={`clinical-action-urgency-${index}`}
                style={{
                  margin: 0,
                  background: `${meta.color}15`,
                  border: `1px solid ${meta.color}40`,
                  color: meta.color,
                  fontSize: 10,
                  fontFamily: tokens.font.mono,
                }}
              >
                {meta.label}
              </Tag>
              <Text style={{ color: tokens.color.text.primary, fontSize: 13, fontWeight: 600 }}>
                {action.description ?? action.action_type}
              </Text>
              {action.target_department && (
                <Text style={{ color: tokens.color.text.secondary, fontSize: 11 }}>
                  {action.target_department}
                </Text>
              )}
              {action.applicable_level && levelColor && (
                <Tag
                  data-testid={`clinical-action-level-${index}`}
                  style={{
                    margin: 0,
                    background: `${levelColor}15`,
                    border: `1px solid ${levelColor}40`,
                    color: levelColor,
                    fontSize: 10,
                  }}
                >
                  适用：{action.applicable_level}
                </Tag>
              )}
            </div>
            <Text
              style={{
                color: tokens.color.text.muted,
                fontSize: 10,
                fontFamily: tokens.font.mono,
              }}
            >
              {action.action_type}
              {action.trigger_condition ? ` · 触发：${action.trigger_condition}` : ''}
            </Text>
          </div>
        );
      })}
    </div>
  );
}
