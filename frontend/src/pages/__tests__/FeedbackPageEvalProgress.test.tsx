/**
 * A.61：反馈页「评测集进度」只读区块的三种真实形态。
 *
 * 为什么值得守：19 号要的是「进度条」，而进度条最容易犯的错是**显示一个假的 0/700**
 * （没采集、采集失败、报告损坏都渲染成 0%）—— 医生会以为"系统在采但没进展"，
 * 与「零静默」直接冲突。三种形态都必须给出**可读原因**。
 */
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

const get = vi.fn();
vi.mock('@/services/api', () => ({
  default: { get: (...args: unknown[]) => get(...args), post: vi.fn() },
}));

import FeedbackPage from '../FeedbackPage';

function statsBody(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      available: true,
      generated_at: '2026-10-13 20:10:00',
      source: 'business_db',
      total: 120,
      deduped: 100,
      undedupable: 3,
      skipped_no_image: 17,
      patient_count: 12,
      patient_id_missing: 0,
      dedup_reason: { patient_image: 120 },
      doctor_labeled: 5,
      targets: { total: 700, doctor_labeled: 50 },
      progress: { samples: 100 / 700, doctor_labeled: 0.1 },
      dedup_basis: { sop_instance_uid: 97, none: 3 },
      ...overrides,
    },
  };
}

describe('FeedbackPage · 评测集进度区块（只读）', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('有报告：渲染样本与医生标注坏例两条进度（含目标值）', async () => {
    get.mockResolvedValue(statsBody());
    const { container } = render(<FeedbackPage />);

    expect(container.querySelector('[data-region="eval-set-progress"]')).not.toBeNull();
    await waitFor(() => {
      expect(screen.getByText(/样本（去重后）：100 \/ 700/)).toBeInTheDocument();
    });
    expect(screen.getByText(/医生标注坏例（含正确口径）：5 \/ 50/)).toBeInTheDocument();
    expect(screen.getByText(/判读总数 120 · 无法去重 3/)).toBeInTheDocument();
    // A.62 批 3：患者维度上屏 —— 「700 例」的真实刻度是不同患者数
    expect(screen.getByText(/不同患者 12 人/)).toBeInTheDocument();
    expect(screen.getByText(/patient_image:120/)).toBeInTheDocument();
  });

  it('尚未采集（available=false）：给可读原因，不显示假的 0/700 进度条', async () => {
    get.mockResolvedValue(
      statsBody({ available: false, reason: '尚未采集（在服务器跑 scripts/langfuse/collect_eval_set.py）' }),
    );
    render(<FeedbackPage />);

    await waitFor(() => {
      expect(screen.getByText(/尚未采集：尚未采集（在服务器跑/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/样本（去重后）/)).toBeNull();
  });

  it('读取失败：页面不崩，给出可读提示（零静默）', async () => {
    get.mockRejectedValue(new Error('boom'));
    render(<FeedbackPage />);

    await waitFor(() => {
      expect(screen.getByText(/评测集进度读取失败/)).toBeInTheDocument();
    });
    // 反馈表单仍在（区块失败不拖累主任务）
    expect(screen.getByText('提交反馈')).toBeInTheDocument();
  });
});
