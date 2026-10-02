import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { tokens } from '@/styles/tokens';
import type { DifferentialDiagnosis } from '@/types/api';
import DifferentialDiagnosisList from '../DifferentialDiagnosisList';
import { confidenceColor } from '../displayConstants';

function _items(): DifferentialDiagnosis[] {
  return [
    {
      rank: 1,
      disease_name: '乳头状癌',
      disease_code: 'UDT-THYROID-PTC',
      confidence: 0.6,
      supporting_evidence: ['微钙化', '低回声'],
      excluding_evidence: ['边界清晰'],
    },
    {
      rank: 2,
      disease_name: '结节性甲状腺肿',
      confidence: 0.25,
      supporting_evidence: ['混合回声'],
      excluding_evidence: [],
    },
    {
      rank: 3,
      disease_name: '亚急性甲状腺炎',
      confidence: null,
      supporting_evidence: ['片状低回声'],
      excluding_evidence: [],
    },
  ];
}

describe('DifferentialDiagnosisList（M4 鉴别诊断清单）', () => {
  it('无数据时不渲染', () => {
    const { container } = render(<DifferentialDiagnosisList items={[]} />);
    expect(container).toBeEmptyDOMElement();

    const { container: nullContainer } = render(<DifferentialDiagnosisList items={null} />);
    expect(nullContainer).toBeEmptyDOMElement();
  });

  it('按 rank 升序渲染候选与可能性', () => {
    render(<DifferentialDiagnosisList items={_items()} />);

    expect(screen.getByTestId('differential-item-1')).toHaveTextContent('乳头状癌');
    expect(screen.getByTestId('differential-confidence-1')).toHaveTextContent('60%');
    expect(screen.getByTestId('differential-confidence-2')).toHaveTextContent('25%');
  });

  it('即使传入乱序也按 rank 升序展示', () => {
    const reversed = [..._items()].reverse();
    render(<DifferentialDiagnosisList items={reversed} />);

    const list = screen.getByTestId('differential-diagnosis-list');
    const firstItem = list.querySelector('[data-testid="differential-item-1"]');
    const thirdItem = list.querySelector('[data-testid="differential-item-3"]');
    expect(firstItem).not.toBeNull();
    expect(
      firstItem!.compareDocumentPosition(thirdItem!) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });

  it('可能性缺失时显示"—"并说明原因（不反推数值）', () => {
    render(<DifferentialDiagnosisList items={_items()} />);

    expect(screen.getByTestId('differential-confidence-3')).toHaveTextContent('—');
    expect(screen.getByText('模型未给出该候选的可能性')).toBeInTheDocument();
  });

  it('可能性缺失时不渲染概率条', () => {
    render(<DifferentialDiagnosisList items={_items()} />);

    expect(screen.queryByTestId('differential-bar-3')).not.toBeInTheDocument();
    expect(screen.getByTestId('differential-bar-1')).toBeInTheDocument();
  });

  it('默认不展示依据，展开后显示支持与排除', async () => {
    const user = userEvent.setup();
    render(<DifferentialDiagnosisList items={_items()} />);

    expect(screen.queryByTestId('differential-evidence-1')).not.toBeInTheDocument();

    await user.click(screen.getAllByRole('button')[0]);

    const evidence = screen.getByTestId('differential-evidence-1');
    expect(evidence).toHaveTextContent('支持：微钙化、低回声');
    expect(evidence).toHaveTextContent('排除：边界清晰');
  });

  it('展示病种编码', () => {
    render(<DifferentialDiagnosisList items={_items()} />);

    expect(screen.getByTestId('differential-item-1')).toHaveTextContent('UDT-THYROID-PTC');
  });

  it('无依据的候选不显示展开按钮', () => {
    render(
      <DifferentialDiagnosisList
        items={[
          {
            rank: 1,
            disease_name: '某病',
            confidence: 0.5,
            supporting_evidence: [],
            excluding_evidence: [],
          },
        ]}
      />
    );

    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('置信度颜色按规格分档（>0.7 绿 / 0.4-0.7 黄 / <0.4 红）', () => {
    expect(confidenceColor(0.8)).toBe(tokens.color.accent.success);
    expect(confidenceColor(0.7)).toBe(tokens.color.accent.warning);
    expect(confidenceColor(0.4)).toBe(tokens.color.accent.warning);
    expect(confidenceColor(0.39)).toBe(tokens.color.accent.danger);
    expect(confidenceColor(null)).toBe(tokens.color.text.muted);
  });
});
