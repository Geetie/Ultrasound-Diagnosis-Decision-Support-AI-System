import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { CrossModalityHint } from '@/types/api';
import type { EvidenceData } from '../evidence';
import EvidenceTrace from '../EvidenceTrace';

const EVIDENCE: EvidenceData = {
  reasoning_path: [{ step: 'grading', description: '按 TI-RADS 计分', confidence: 0.8 }],
  knowledge_refs: [{ source: 'kb/thyroid.md', version: 'v1.0.0', relevance: 0.9 }],
  confidence_score: 0.72,
  cached: false,
};

function _hints(): CrossModalityHint[] {
  return [
    {
      modality: 'CT',
      finding: 'CT 对微钙化的敏感性低于超声，常难以显示',
      clinical_value: 'CT 阴性不能排除微钙化',
      source_reference: 'ACR TI-RADS 2017（定性）',
    },
    {
      modality: 'pathology',
      finding: '微钙化对应砂粒体',
      clinical_value: '病理为最终确诊依据',
      source_reference: 'ACR TI-RADS 2017（定性）',
    },
  ];
}

describe('EvidenceTrace 跨模态对照子标签（M12）', () => {
  it('无对照提示时不展示该子标签', () => {
    render(<EvidenceTrace evidence={EVIDENCE} />);

    expect(screen.queryByText(/跨模态对照/)).not.toBeInTheDocument();
  });

  it('传空数组时同样不展示', () => {
    render(<EvidenceTrace evidence={EVIDENCE} crossModalityHints={[]} />);

    expect(screen.queryByText(/跨模态对照/)).not.toBeInTheDocument();
  });

  it('有提示时展示子标签与条数', () => {
    render(<EvidenceTrace evidence={EVIDENCE} crossModalityHints={_hints()} />);

    expect(screen.getByText(/跨模态对照 \(2\)/)).toBeInTheDocument();
  });

  it('展开后展示模态、发现与临床价值', async () => {
    const user = userEvent.setup();
    render(<EvidenceTrace evidence={EVIDENCE} crossModalityHints={_hints()} />);

    await user.click(screen.getByText(/跨模态对照/));

    expect(screen.getByTestId('cross-modality-tag-0')).toHaveTextContent('CT');
    expect(screen.getByTestId('cross-modality-hint-0')).toHaveTextContent('敏感性低于超声');
    expect(screen.getByTestId('cross-modality-hint-0')).toHaveTextContent('CT 阴性不能排除微钙化');
    expect(screen.getByTestId('cross-modality-tag-1')).toHaveTextContent('pathology');
  });

  it('展示文献出处，便于医生核对', async () => {
    const user = userEvent.setup();
    render(<EvidenceTrace evidence={EVIDENCE} crossModalityHints={_hints()} />);

    await user.click(screen.getByText(/跨模态对照/));

    expect(screen.getByTestId('cross-modality-hint-0')).toHaveTextContent('ACR TI-RADS 2017');
  });

  it('跨模态子标签与原有两个子标签并存', () => {
    render(<EvidenceTrace evidence={EVIDENCE} crossModalityHints={_hints()} />);

    expect(screen.getByText(/推理路径 \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/知识库引用 \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/跨模态对照 \(2\)/)).toBeInTheDocument();
  });
});
