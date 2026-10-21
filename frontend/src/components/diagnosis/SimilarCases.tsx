import { Tag, Typography } from 'antd';
import { tokens } from '@/styles/tokens';
import type { SimilarCasesBlock } from '@/types/api';

const { Text } = Typography;

/**
 * 相似病例（A.82）——**本人历史影像对照**。
 *
 * 临床安全约束（与后端契约一致，前端不得弱化）：
 * 1. **标题用后端给的 `label`**（「本人历史影像（参考）」），不得改写成「相似病例诊断」；
 * 2. **免责声明必须显示**，不得折叠默认隐藏——它是"这不是诊断依据"的唯一提示；
 * 3. 只显示 `date / image_id / score`；后端已剔除 patient_id/session_id，前端**不得**再展示任何病患标识；
 * 4. `status !== 'ok'` 或无病例时**整块不渲染**（显示"未检出"会被误读成"没查到相似病例"）。
 *
 * 为什么单独一块、不并进证据链：证据链是"结论的依据"，相似病例只是"既往影像参考"，
 * 混在一起会让医生以为本次结论引用了这些历史影像。
 */
interface SimilarCasesProps {
  block: SimilarCasesBlock | null | undefined;
}

export default function SimilarCases({ block }: SimilarCasesProps) {
  if (!block || block.status !== 'ok') {
    return null;
  }
  const cases = block.cases ?? [];
  if (cases.length === 0) {
    return null;
  }

  return (
    <div
      data-testid="similar-cases"
      style={{
        background: tokens.color.bg.secondary,
        border: `1px solid ${tokens.color.border.default}`,
        borderLeft: `3px solid ${tokens.color.accent.info}40`,
        borderRadius: tokens.radius.md,
        padding: `${tokens.spacing.sm}px ${tokens.spacing.md}px`,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: tokens.spacing.sm,
          flexWrap: 'wrap',
          marginBottom: tokens.spacing.xs,
        }}
      >
        <Text
          style={{
            fontFamily: tokens.font.display,
            color: tokens.color.accent.info,
            fontSize: 12,
            letterSpacing: '0.05em',
          }}
        >
          PRIOR IMAGING
        </Text>
        <Tag color="blue">{block.label}</Tag>
        <Text type="secondary" style={{ fontSize: 11 }}>
          {block.count} 次既往检查
        </Text>
      </div>

      <Text
        type="secondary"
        data-testid="similar-cases-disclaimer"
        style={{ display: 'block', fontSize: 11, marginBottom: tokens.spacing.xs }}
      >
        {block.disclaimer}
      </Text>

      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {cases.map((item) => (
          <li key={item.image_id} style={{ fontSize: 12, lineHeight: 1.7 }}>
            <Text>{item.date ?? '日期未知'}</Text>
            <Text type="secondary" style={{ marginLeft: 8 }}>
              {item.image_id}
            </Text>
            <Text type="secondary" style={{ marginLeft: 8 }}>
              相似度 {item.score.toFixed(3)}
            </Text>
          </li>
        ))}
      </ul>
    </div>
  );
}
