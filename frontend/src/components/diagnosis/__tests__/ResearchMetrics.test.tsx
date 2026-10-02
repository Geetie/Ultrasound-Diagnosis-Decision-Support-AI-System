import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { ResearchMetrics as ResearchMetricsData } from '@/types/api';
import ResearchMetrics from '../ResearchMetrics';

function _metrics(overrides: Partial<ResearchMetricsData> = {}): ResearchMetricsData {
  return {
    elasticity_score: 3,
    elasticity_unit: 'score',
    blood_flow: { psv: 45, edv: 12, ri: 0.72, unit: 'cm/s' },
    ai_features: { texture_homogeneity: null, edge_sharpness: null, feature_vector_hash: '58104555db761dcb' },
    confidence_distribution: { benign: 0.0936, malignant: 0.87, indeterminate: 0.0364 },
    ...overrides,
  };
}

describe('ResearchMetrics（M11 科研量化指标）', () => {
  it('无指标时不渲染', () => {
    const { container } = render(<ResearchMetrics metrics={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('默认折叠，仅展示标题与用途说明', () => {
    render(<ResearchMetrics metrics={_metrics()} />);

    expect(screen.getByText('RESEARCH METRICS')).toBeInTheDocument();
    expect(screen.getByText('科研用途，非临床决策依据')).toBeInTheDocument();
    expect(screen.queryByTestId('research-metrics-body')).not.toBeInTheDocument();
  });

  it('展开后展示弹性评分、血流参数与特征指纹', async () => {
    const user = userEvent.setup();
    render(<ResearchMetrics metrics={_metrics()} />);

    await user.click(screen.getByRole('button', { name: '展开' }));

    const body = screen.getByTestId('research-metrics-body');
    expect(body).toHaveTextContent('3 score');
    expect(body).toHaveTextContent('PSV 45');
    expect(body).toHaveTextContent('EDV 12');
    expect(body).toHaveTextContent('RI 0.72');
    expect(body).toHaveTextContent('cm/s');
    expect(body).toHaveTextContent('58104555db761dcb');
  });

  it('未提供弹性/血流数据时给出说明而非编造数值', async () => {
    const user = userEvent.setup();
    render(
      <ResearchMetrics
        metrics={_metrics({
          elasticity_score: null,
          blood_flow: null,
          ai_features: { texture_homogeneity: null, edge_sharpness: null, feature_vector_hash: null },
        })}
      />
    );

    await user.click(screen.getByRole('button', { name: '展开' }));

    expect(screen.getByTestId('research-metrics-body')).toHaveTextContent('本次未提取到弹性/血流参数');
  });

  it('只有特征指纹时不出现弹性/血流行（无来源不填数）', async () => {
    const user = userEvent.setup();
    render(<ResearchMetrics metrics={_metrics({ elasticity_score: null, blood_flow: null })} />);

    await user.click(screen.getByRole('button', { name: '展开' }));

    const body = screen.getByTestId('research-metrics-body');
    expect(body).toHaveTextContent('特征指纹');
    expect(body).not.toHaveTextContent('PSV');
    expect(body).not.toHaveTextContent('弹性评分');
    expect(body).not.toHaveTextContent('本次未提取到');
  });

  it('置信度分布渲染三段并带文字标签（不单靠颜色）', async () => {
    const user = userEvent.setup();
    render(<ResearchMetrics metrics={_metrics()} />);

    await user.click(screen.getByRole('button', { name: '展开' }));

    expect(screen.getByTestId('distribution-benign')).toHaveStyle({ width: '9.36%' });
    expect(screen.getByTestId('distribution-malignant')).toHaveStyle({ width: '87%' });
    expect(screen.getByTestId('distribution-label-benign')).toHaveTextContent('良性 9%');
    expect(screen.getByTestId('distribution-label-malignant')).toHaveTextContent('恶性 87%');
    expect(screen.getByTestId('distribution-label-indeterminate')).toHaveTextContent('不确定 4%');
  });

  it('明确标注置信度分布是派生视图（非模型原始分布）', async () => {
    const user = userEvent.setup();
    render(<ResearchMetrics metrics={_metrics()} />);

    await user.click(screen.getByRole('button', { name: '展开' }));

    expect(screen.getByText(/非模型输出的原始分布/)).toBeInTheDocument();
  });

  it('无非置信度分布时不渲染该区块', async () => {
    const user = userEvent.setup();
    render(<ResearchMetrics metrics={_metrics({ confidence_distribution: null })} />);

    await user.click(screen.getByRole('button', { name: '展开' }));

    expect(screen.queryByTestId('confidence-distribution')).not.toBeInTheDocument();
  });

  it('defaultExpanded 可让科研模式直接展开', () => {
    render(<ResearchMetrics metrics={_metrics()} defaultExpanded />);

    expect(screen.getByTestId('research-metrics-body')).toBeInTheDocument();
  });
});
