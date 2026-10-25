import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import WorkspaceReportPanel from '../WorkspaceReportPanel';

/**
 * 报告侧面板的**数据口径**（T7 生产可达的前提）。
 *
 * 核心风险：组件里有一段设计画布写死的示意文字（「18.4 × 12.1 mm 低回声结节」）。
 * 一旦生产入口打开，如果面板在没有真实正文时**回落**到这段文字，就等于在真实
 * 患者界面上显示**伪造的超声所见**。所以：
 *   · 传了 reportText → 只显示真数据，画布文字一个字都不能出现；
 *   · 没传（仅 dev `?state=` 后门）→ 才是画布预览。
 */
describe('WorkspaceReportPanel · 真实正文优先', () => {
  const CANVAS_SNIPPETS = [/18\.4 × 12\.1 mm/, /TI-RADS 3 类/, /甲状腺右叶中极/];

  it('传了 reportText 就只显示后端正文，画布示意文字不出现', () => {
    const { container } = render(
      <WorkspaceReportPanel reportText="所见：肝脏大小形态正常，实质回声均匀。" />
    );

    expect(screen.getByText(/肝脏大小形态正常/)).toBeInTheDocument();
    for (const snippet of CANVAS_SNIPPETS) {
      expect(container.textContent).not.toMatch(snippet);
    }
    // 有真数据时走的是 report-body 分支
    expect(container.querySelector('[data-region="report-body"]')).not.toBeNull();
  });

  it('没有 reportText 时才回落到画布预览（仅 dev 后门）', () => {
    const { container } = render(<WorkspaceReportPanel />);
    expect(container.textContent).toMatch(/18\.4 × 12\.1 mm/);
    expect(container.querySelector('[data-region="report-body"]')).toBeNull();
  });

  it('空字符串不算真实正文（不回落到画布，但也不渲染 body）', () => {
    const { container } = render(<WorkspaceReportPanel reportText="   " />);
    // 空白正文按"没有正文"处理 → 回落画布预览（dev 行为）
    expect(container.querySelector('[data-region="report-body"]')).toBeNull();
  });

  it('statusNote 有值时替代画布那句"系统预生成"', () => {
    render(
      <WorkspaceReportPanel
        reportText="所见：真实正文。"
        statusNote="该报告尚无渲染正文 —— 请到「报告」页生成草稿"
      />
    );
    expect(screen.getByText(/尚无渲染正文/)).toBeInTheDocument();
    expect(screen.queryByText(/草稿由系统预生成/)).toBeNull();
  });

  it('signed 由调用方（后端状态）决定：标题与签发按钮同步', () => {
    const onSign = vi.fn();
    const { container, rerender } = render(<WorkspaceReportPanel reportText="正文" />);
    expect(container.querySelector('.ws-report-panel__title')!.textContent).toBe('报告草稿');
    expect(screen.getByRole('button', { name: '签发' })).toBeEnabled();

    rerender(<WorkspaceReportPanel reportText="正文" signed onSign={onSign} />);
    expect(container.querySelector('.ws-report-panel__title')!.textContent).toBe('已签发');
    expect(screen.getByRole('button', { name: '已签发' })).toBeDisabled();
  });
});
