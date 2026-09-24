import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { ClinicalAction } from '@/types/api';
import ClinicalActions from '../ClinicalActions';

function _action(overrides: Partial<ClinicalAction> = {}): ClinicalAction {
  return {
    action_type: 'fine_needle_aspiration',
    target_department: '甲状腺外科',
    urgency: 'urgent',
    trigger_condition: '恶性风险高',
    description: '建议行细针穿刺细胞学检查',
    applicable_level: '三甲',
    source_reference: 'ACR TI-RADS 2017',
    ...overrides,
  };
}

describe('ClinicalActions（M7 处置建议）', () => {
  it('无建议时不渲染（不展示"无需处置"这种误导性结论）', () => {
    const { container } = render(<ClinicalActions actions={[]} />);
    expect(container).toBeEmptyDOMElement();

    const { container: nullContainer } = render(<ClinicalActions actions={null} />);
    expect(nullContainer).toBeEmptyDOMElement();
  });

  it('展示动作、科室与触发条件', () => {
    render(<ClinicalActions actions={[_action()]} />);

    const item = screen.getByTestId('clinical-action-0');
    expect(item).toHaveTextContent('建议行细针穿刺细胞学检查');
    expect(item).toHaveTextContent('甲状腺外科');
    expect(item).toHaveTextContent('fine_needle_aspiration');
    expect(item).toHaveTextContent('触发：恶性风险高');
  });

  it('紧急度带文字标签（不单靠颜色）', () => {
    render(<ClinicalActions actions={[_action({ urgency: 'urgent' })]} />);
    expect(screen.getByTestId('clinical-action-urgency-0')).toHaveTextContent('紧急');
  });

  it('按紧急度排序：urgent → soon → routine', () => {
    render(
      <ClinicalActions
        actions={[
          _action({ action_type: 'annual_followup', urgency: 'routine' }),
          _action({ action_type: 'contrast_imaging', urgency: 'urgent' }),
          _action({ action_type: 'short_interval_followup', urgency: 'soon' }),
        ]}
      />
    );

    expect(screen.getByTestId('clinical-action-0')).toHaveAttribute('data-urgency', 'urgent');
    expect(screen.getByTestId('clinical-action-1')).toHaveAttribute('data-urgency', 'soon');
    expect(screen.getByTestId('clinical-action-2')).toHaveAttribute('data-urgency', 'routine');
  });

  it('标注适用机构层级（基层做不了穿刺，必须显性化）', () => {
    render(<ClinicalActions actions={[_action({ applicable_level: '基层' })]} />);
    expect(screen.getByTestId('clinical-action-level-0')).toHaveTextContent('适用：基层');
  });

  it('无适用层级时不显示层级标签', () => {
    render(<ClinicalActions actions={[_action({ applicable_level: null })]} />);
    expect(screen.queryByTestId('clinical-action-level-0')).not.toBeInTheDocument();
  });

  it('缺少 description 时回退展示动作类型', () => {
    render(<ClinicalActions actions={[_action({ description: null, action_type: 'cardiology_referral' })]} />);
    expect(screen.getByTestId('clinical-action-0')).toHaveTextContent('cardiology_referral');
  });

  it('未知紧急度按常规处理而不报错', () => {
    render(
      <ClinicalActions
        actions={[_action({ urgency: 'unknown_level' as ClinicalAction['urgency'] })]}
      />
    );
    expect(screen.getByTestId('clinical-action-urgency-0')).toHaveTextContent('常规');
  });
});
