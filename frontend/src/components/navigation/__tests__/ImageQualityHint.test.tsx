import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { ImageQuality } from '@/types/api';
import ImageQualityHint from '../ImageQualityHint';

function _quality(overrides: Partial<ImageQuality> = {}): ImageQuality {
  return {
    overall_score: 0.6,
    blur_score: 0.7,
    artifact_score: 0.9,
    gain_score: 0.8,
    issues: [],
    diagnostic_impact: 'minor',
    ...overrides,
  };
}

describe('ImageQualityHint（M6 图像质量评估）', () => {
  it('质量达标（≥0.7）时不渲染任何提示', () => {
    render(<ImageQualityHint quality={_quality({ overall_score: 0.85 })} />);

    expect(screen.queryByTestId('image-quality-hint')).not.toBeInTheDocument();
  });

  it('无质量数据时不渲染', () => {
    const { container } = render(<ImageQualityHint quality={null} />);

    expect(container).toBeEmptyDOMElement();
  });

  it('0.5-0.7 显示黄色警示', () => {
    render(<ImageQualityHint quality={_quality({ overall_score: 0.58 })} />);

    expect(screen.getByTestId('image-quality-hint')).toHaveAttribute('data-level', 'warning');
    expect(screen.getByText('图像质量欠佳')).toBeInTheDocument();
  });

  it('<0.5 显示严重提示并说明可能影响判读', () => {
    render(
      <ImageQualityHint
        quality={_quality({ overall_score: 0.32, issues: ['image_too_dark', 'severe_blur'] })}
      />
    );

    expect(screen.getByTestId('image-quality-hint')).toHaveAttribute('data-level', 'severe');
    expect(screen.getByText('图像质量差 — 可能影响判读')).toBeInTheDocument();
    expect(screen.getByText(/图像过暗/)).toBeInTheDocument();
  });

  it('展开后展示分项分数', async () => {
    const user = userEvent.setup();
    render(<ImageQualityHint quality={_quality({ overall_score: 0.4, blur_score: 0.25 })} />);

    expect(screen.queryByTestId('image-quality-detail')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button'));

    const detail = screen.getByTestId('image-quality-detail');
    expect(detail).toHaveTextContent('清晰度：25%');
    expect(detail).toHaveTextContent('伪影：90%');
    expect(detail).toHaveTextContent('增益：80%');
    expect(detail).toHaveTextContent('判读影响：minor');
  });

  it('分项缺失时显示占位而不报错', async () => {
    const user = userEvent.setup();
    render(
      <ImageQualityHint
        quality={_quality({ overall_score: 0.3, blur_score: null, artifact_score: null })}
      />
    );

    await user.click(screen.getByRole('button'));

    expect(screen.getByTestId('image-quality-detail')).toHaveTextContent('清晰度：—');
  });
});
