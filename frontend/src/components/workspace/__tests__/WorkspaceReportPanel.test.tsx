import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import WorkspaceReportPanel from '../WorkspaceReportPanel';

/**
 * 报告侧面板的**数据口径**（T7 生产可达的前提）。
 *
 * 原本的核心风险：组件里有一段设计画布写死的示意文字（「18.4 × 12.1 mm 低回声结节」），
 * 一旦生产入口打开、面板在没有真实正文时回落 → 等于在真实患者界面上显示**伪造的超声所见**。
 *
 * **2026-10-28 修（P1-8）**：那段画布示例文字已被**整段删除**。现在无正文时只显示
 * 空态提示（`data-region="report-empty"`），任何临床内容都不编造。
 * 所以本文件的断言从"没传时回落画布"改成"没传时**也不出现**画布文字，只出空态"。
 *
 * 同时覆盖 P0-1/P0-2：两个按钮（签发 / 结构化校对）在回调缺失时必须**禁用并说明**，
 * 不能像修复前那样 `onClick={undefined}` 却看起来可点。
 */
describe('WorkspaceReportPanel · 真实正文优先（不再有画布示例文字）', () => {
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
    expect(container.querySelector('[data-region="report-empty"]')).toBeNull();
  });

  it('**没有 reportText 时也不出现画布示例文字**，只出空态（P1-8 修复的反证腿）', () => {
    const { container } = render(<WorkspaceReportPanel />);
    for (const snippet of CANVAS_SNIPPETS) {
      expect(container.textContent).not.toMatch(snippet);
    }
    expect(container.querySelector('[data-region="report-body"]')).toBeNull();
    expect(container.querySelector('[data-region="report-empty"]')).not.toBeNull();
    // 默认空态文案
    expect(screen.getByText('尚无渲染正文')).toBeInTheDocument();
  });

  it('空字符串算没有正文：不渲染 body、不编造内容', () => {
    const { container } = render(<WorkspaceReportPanel reportText="   " />);
    expect(container.querySelector('[data-region="report-body"]')).toBeNull();
    for (const snippet of CANVAS_SNIPPETS) {
      expect(container.textContent).not.toMatch(snippet);
    }
  });

  it('statusNote 有值时替代默认空态文案', () => {
    render(
      <WorkspaceReportPanel
        reportText={null}
        statusNote="该报告尚无渲染正文 —— 请到「报告」页生成草稿"
      />
    );
    expect(screen.getByText(/尚无渲染正文/)).toBeInTheDocument();
    expect(screen.queryByText(/正文来自后端渲染/)).toBeNull();
  });

  /* ------------------------------------------------------------------
     P0-1 / P0-2：两个按钮必须真的能点，或明确禁用
     ------------------------------------------------------------------ */

  it('有正文 + 有 onSign → 签发按钮可点，点击触发回调', () => {
    const onSign = vi.fn();
    render(<WorkspaceReportPanel reportText="正文" onSign={onSign} />);
    const btn = screen.getByRole('button', { name: '签发' });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(onSign).toHaveBeenCalledTimes(1);
  });

  it('**没有 onSign 时签发按钮禁用**（修复前是 onClick=undefined 但看起来可点）', () => {
    render(<WorkspaceReportPanel reportText="正文" />);
    const btn = screen.getByRole('button', { name: '签发' });
    expect(btn).toBeDisabled();
    expect(btn.getAttribute('title')).toMatch(/签发通道未接线/);
  });

  it('**没有正文时签发按钮也禁用**（无正文不能签）', () => {
    const onSign = vi.fn();
    render(<WorkspaceReportPanel onSign={onSign} />);
    const btn = screen.getByRole('button', { name: '签发' });
    expect(btn).toBeDisabled();
    expect(btn.getAttribute('title')).toMatch(/尚无渲染正文/);
  });

  it('结构化校对：有回调可点、无回调禁用', () => {
    const onProofread = vi.fn();
    const { rerender } = render(
      <WorkspaceReportPanel reportText="正文" onStructuredProofread={onProofread} />
    );
    const btn = screen.getByRole('button', { name: '结构化校对' });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(onProofread).toHaveBeenCalledTimes(1);

    rerender(<WorkspaceReportPanel reportText="正文" />);
    expect(screen.getByRole('button', { name: '结构化校对' })).toBeDisabled();
  });

  it('signing=true 时签发按钮禁用并显示「签发中…」（防连点重复签名）', () => {
    const onSign = vi.fn();
    render(<WorkspaceReportPanel reportText="正文" onSign={onSign} signing />);
    const btn = screen.getByRole('button', { name: '签发中…' });
    expect(btn).toBeDisabled();
  });

  it('signed 由调用方（后端状态）决定：标题与签发按钮同步', () => {
    const onSign = vi.fn();
    const { container, rerender } = render(
      <WorkspaceReportPanel reportText="正文" onSign={onSign} />
    );
    expect(container.querySelector('.ws-report-panel__title')!.textContent).toBe('报告草稿');
    expect(screen.getByRole('button', { name: '签发' })).toBeEnabled();

    rerender(<WorkspaceReportPanel reportText="正文" signed onSign={onSign} />);
    expect(container.querySelector('.ws-report-panel__title')!.textContent).toBe('已签发');
    expect(screen.getByRole('button', { name: '已签发' })).toBeDisabled();
  });
});
