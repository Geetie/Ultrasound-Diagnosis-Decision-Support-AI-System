import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { PlaneAssessment } from '@/types/api';
import PlaneStatusBadge from '../PlaneStatusBadge';

function _assessment(overrides: Partial<PlaneAssessment> = {}): PlaneAssessment {
  return {
    plane_type: 'longitudinal',
    is_standard: true,
    plane_score: 0.92,
    organ_code: 'thyroid',
    issues: [],
    required_structures_visible: ['left_lobe', 'right_lobe', 'isthmus'],
    ...overrides,
  };
}

describe('PlaneStatusBadge（M1 切面标准性评估）', () => {
  it('无数据时显示"切面无数据"，与"切面不合格"区分开', () => {
    render(<PlaneStatusBadge assessment={null} />);

    expect(screen.getByTestId('plane-status-badge')).toHaveAttribute('data-level', 'unknown');
    expect(screen.getByText('切面无数据')).toBeInTheDocument();
  });

  it('标准切面显示绿色通过状态与分数', () => {
    render(<PlaneStatusBadge assessment={_assessment()} />);

    expect(screen.getByTestId('plane-status-badge')).toHaveAttribute('data-level', 'standard');
    expect(screen.getByText('切面标准')).toBeInTheDocument();
    expect(screen.getByText('92%')).toBeInTheDocument();
  });

  it('非标准切面显示预警状态', () => {
    render(
      <PlaneStatusBadge
        assessment={_assessment({
          is_standard: false,
          plane_score: 0.35,
          issues: ['plane_below_standard', 'low_contrast'],
        })}
      />
    );

    expect(screen.getByTestId('plane-status-badge')).toHaveAttribute('data-level', 'warning');
    expect(screen.getByText('切面非标准')).toBeInTheDocument();
  });

  it('始终带文字标签，不单靠颜色传意（无障碍）', () => {
    render(<PlaneStatusBadge assessment={_assessment({ is_standard: false })} />);

    expect(screen.getByLabelText('切面状态：切面非标准')).toBeInTheDocument();
  });

  it('展开后显示切面类型、问题与可见结构', async () => {
    const user = userEvent.setup();
    render(
      <PlaneStatusBadge
        assessment={_assessment({
          is_standard: false,
          plane_type: 'non_standard',
          issues: ['image_too_dark'],
        })}
      />
    );

    expect(screen.queryByTestId('plane-status-detail')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button'));

    const detail = screen.getByTestId('plane-status-detail');
    expect(detail).toHaveTextContent('非标准切面');
    expect(detail).toHaveTextContent('图像过暗');
    expect(detail).toHaveTextContent('left_lobe');
  });

  it('未知问题码原样展示，避免信息丢失', async () => {
    const user = userEvent.setup();
    render(<PlaneStatusBadge assessment={_assessment({ issues: ['brand_new_issue'] })} />);

    await user.click(screen.getByRole('button'));

    expect(screen.getByTestId('plane-status-detail')).toHaveTextContent('brand_new_issue');
  });
});
