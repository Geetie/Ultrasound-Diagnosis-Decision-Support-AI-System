import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { PatientExplanation as PatientExplanationData } from '@/types/api';
import PatientExplanation from '../PatientExplanation';

function _data(overrides: Partial<PatientExplanationData> = {}): PatientExplanationData {
  return {
    explanation: '结节多数为良性，本次超声未见明显恶性征象',
    reassurance: '不必过度担心，规律复查即可',
    next_step: '建议 6 个月后复查超声',
    source_reference: '模型生成（需医生确认后方可告知患者）',
    ...overrides,
  };
}

describe('PatientExplanation（M10 患者解释话术）', () => {
  it('无话术时不渲染（不拼凑安抚话）', () => {
    const { container } = render(<PatientExplanation explanation={null} />);
    expect(container).toBeEmptyDOMElement();

    const { container: undefinedContainer } = render(<PatientExplanation explanation={undefined} />);
    expect(undefinedContainer).toBeEmptyDOMElement();
  });

  it('默认折叠，仅展示标题与免责标注', () => {
    render(<PatientExplanation explanation={_data()} />);

    expect(screen.getByTestId('patient-explanation-disclaimer')).toHaveTextContent('非正式报告内容');
    expect(screen.queryByTestId('patient-explanation-body')).not.toBeInTheDocument();
  });

  it('展开后展示解释、安抚与下一步', async () => {
    const user = userEvent.setup();
    render(<PatientExplanation explanation={_data()} />);

    await user.click(screen.getByRole('button', { name: '展开' }));

    expect(screen.getByTestId('patient-explanation-解释')).toHaveTextContent('结节多数为良性');
    expect(screen.getByTestId('patient-explanation-安抚')).toHaveTextContent('不必过度担心');
    expect(screen.getByTestId('patient-explanation-下一步')).toHaveTextContent('6 个月后复查超声');
  });

  it('缺失字段不渲染对应行', async () => {
    const user = userEvent.setup();
    render(
      <PatientExplanation explanation={_data({ reassurance: null, next_step: null })} />
    );

    await user.click(screen.getByRole('button', { name: '展开' }));

    expect(screen.getByTestId('patient-explanation-解释')).toBeInTheDocument();
    expect(screen.queryByTestId('patient-explanation-安抚')).not.toBeInTheDocument();
    expect(screen.queryByTestId('patient-explanation-下一步')).not.toBeInTheDocument();
  });

  it('展示来源说明（提醒需医生确认后才能告知患者）', async () => {
    const user = userEvent.setup();
    render(<PatientExplanation explanation={_data()} />);

    await user.click(screen.getByRole('button', { name: '展开' }));

    expect(screen.getByTestId('patient-explanation-source')).toHaveTextContent('需医生确认');
  });

  it('可切换到患者版视图：隐藏医生向标签行', async () => {
    const user = userEvent.setup();
    render(<PatientExplanation explanation={_data()} />);

    await user.click(screen.getByRole('button', { name: '展开' }));
    expect(screen.getByTestId('patient-explanation-body')).toHaveAttribute('data-view', 'doctor');

    await user.click(screen.getByTestId('patient-explanation-view-toggle'));

    expect(screen.getByTestId('patient-explanation-body')).toHaveAttribute('data-view', 'patient');
    // 患者版只保留话术正文，不显示"解释/安抚/下一步"这类医生向标签
    expect(screen.queryByText('安抚')).not.toBeInTheDocument();
    expect(screen.getByTestId('patient-explanation-安抚')).toHaveTextContent('不必过度担心');
  });

  it('折叠状态下不显示视图切换按钮', () => {
    render(<PatientExplanation explanation={_data()} />);

    expect(screen.queryByTestId('patient-explanation-view-toggle')).not.toBeInTheDocument();
  });
});
