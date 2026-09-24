import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { MalignancyRisk } from '@/types/api';
import MalignancyRiskGauge, { MALIGNANCY_FEATURE_LABELS } from '../MalignancyRiskGauge';

function _risk(overrides: Partial<MalignancyRisk> = {}): MalignancyRisk {
  return {
    probability: 0.62,
    level: 'medium',
    basis: [
      { feature: 'micro', contribution: 0.3 },
      { feature: 'clear', contribution: -0.1 },
    ],
    source_reference: 'ACR TI-RADS 2017（定性描述）',
    ...overrides,
  };
}

describe('MalignancyRiskGauge（M5 恶性概率）', () => {
  it('无数据时不渲染（与"概率为零"区分）', () => {
    const { container } = render(<MalignancyRiskGauge risk={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('展示概率与档位标签', () => {
    render(<MalignancyRiskGauge risk={_risk()} />);

    expect(screen.getByTestId('malignancy-risk-value')).toHaveTextContent('62%');
    expect(screen.getByTestId('malignancy-risk-level')).toHaveTextContent('中');
    expect(screen.getByTestId('malignancy-risk-gauge')).toHaveAttribute('data-level', 'medium');
  });

  it('高档位带"高"文字标签，不单靠颜色传意（无障碍）', () => {
    render(<MalignancyRiskGauge risk={_risk({ level: 'high', probability: 0.9 })} />);
    expect(screen.getByTestId('malignancy-risk-level')).toHaveTextContent('高');
  });

  it('低档位带"低"文字标签', () => {
    render(<MalignancyRiskGauge risk={_risk({ level: 'low', probability: 0.1 })} />);
    expect(screen.getByTestId('malignancy-risk-level')).toHaveTextContent('低');
  });

  it('默认不展示依据明细', () => {
    render(<MalignancyRiskGauge risk={_risk()} />);
    expect(screen.queryByTestId('malignancy-risk-basis')).not.toBeInTheDocument();
  });

  it('展开后展示每条特征的贡献与出处', async () => {
    const user = userEvent.setup();
    render(<MalignancyRiskGauge risk={_risk()} />);

    await user.click(screen.getByRole('button'));

    const basis = screen.getByTestId('malignancy-risk-basis');
    expect(basis).toHaveTextContent('微钙化');
    expect(basis).toHaveTextContent('+0.30');
    expect(basis).toHaveTextContent('边界清晰');
    expect(basis).toHaveTextContent('-0.10');
    expect(basis).toHaveTextContent('ACR TI-RADS 2017');
  });

  it('未知特征码原样展示，避免信息丢失', async () => {
    const user = userEvent.setup();
    render(<MalignancyRiskGauge risk={_risk({ basis: [{ feature: 'brand_new', contribution: 0.05 }] })} />);

    await user.click(screen.getByRole('button'));

    expect(screen.getByTestId('malignancy-risk-basis')).toHaveTextContent('brand_new');
  });

  it('无依据时不显示展开按钮', () => {
    render(<MalignancyRiskGauge risk={_risk({ basis: [] })} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('概率越界时进度条宽度被夹紧', () => {
    render(<MalignancyRiskGauge risk={_risk({ probability: 1.4 })} />);
    expect(screen.getByTestId('malignancy-risk-bar')).toHaveStyle({ width: '100%' });
  });

  it('正常概率映射为对应宽度', () => {
    render(<MalignancyRiskGauge risk={_risk({ probability: 0.62 })} />);
    expect(screen.getByTestId('malignancy-risk-bar')).toHaveStyle({ width: '62%' });
  });

  it('特征标签映射覆盖后端风险模型用到的术语', () => {
    for (const feature of [
      'micro',
      'irregular',
      'ill-defined',
      'hypoechoic',
      'aspect_ratio_taller_than_wide',
      'posterior_attenuation',
      'clear',
      'oval',
      'posterior_shadowing',
      'posterior_enhancement',
      'anechoic',
    ]) {
      expect(MALIGNANCY_FEATURE_LABELS[feature]).toBeTruthy();
    }
  });
});
