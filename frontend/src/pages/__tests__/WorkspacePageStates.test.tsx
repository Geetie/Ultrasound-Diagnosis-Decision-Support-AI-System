import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import WorkspacePage from '../WorkspacePage';

const css = (rel: string) => readFileSync(path.resolve(process.cwd(), rel), 'utf8');
const tokensCss = css('src/styles/design-tokens.css');
const workspaceCss = css('src/styles/workspace.css');

function tokenPx(name: string): number {
  const hit = tokensCss.match(new RegExp(`${name}:\\s*(\\d+)px`));
  if (!hit) throw new Error(`design-tokens.css 缺少 ${name}`);
  return Number(hit[1]);
}

function ruleBody(cssText: string, selector: string): string {
  const hit = cssText.match(new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`));
  if (!hit) throw new Error(`workspace.css 缺少规则 ${selector}`);
  return hit[1];
}

const ALL_STATES = [
  'scanning', 'progress', 'conclusion', 'multi-organ', 'stopped',
  'report', 'actionbar', 'ai-unavailable', 'signed', 'masked', 'organ-tags',
] as const;

describe('WorkspacePage · 11 个视图状态共用同一骨架', () => {
  it('每态都渲染 骨架三区', () => {
    for (const state of ALL_STATES) {
      const { container, unmount } = render(<WorkspacePage state={state} />);
      expect(container.querySelector('[data-region="bar"]')).not.toBeNull();
      expect(container.querySelector('[data-region="image"]')).not.toBeNull();
      expect(container.querySelector('[data-region="conclusion"]')).not.toBeNull();
      unmount();
    }
  });

  it('影像区在所有状态下恒定 600（11 态共用同一个组件）', () => {
    expect(tokenPx('--size-image-h')).toBe(600);
    expect(ruleBody(workspaceCss, '.ws-image')).toMatch(/height:\s*var\(--size-image-h\)/);
    expect(ruleBody(workspaceCss, '.ws-image')).toMatch(/flex-shrink:\s*0/);
    for (const state of ALL_STATES) {
      const { container, unmount } = render(<WorkspacePage state={state} />);
      expect(container.querySelectorAll('[data-region="image"]')).toHaveLength(1);
      unmount();
    }
  });

  it('部位指示条锚在影像区内部，不新增区域', () => {
    for (const state of ALL_STATES) {
      const { container, unmount } = render(<WorkspacePage state={state} />);
      const bar = container.querySelector('[data-region="organ-bar"]');
      if (bar) expect(bar.closest('[data-region="image"]')).not.toBeNull();
      unmount();
    }
  });
});

describe('03_等待分析中', () => {
  it('进度只用「分析中」一个词，不给百分比与预计剩余时间', () => {
    const { container } = render(<WorkspacePage state="progress" />);
    expect(screen.getByText('分析中')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\d+%|预计|剩余/);
  });

  it('保留「不用等」的出口', () => {
    render(<WorkspacePage state="progress" />);
    expect(screen.getByText(/不用等它跑完/)).toBeInTheDocument();
  });

  it('不渲染确认动作区与报告面板', () => {
    const { container } = render(<WorkspacePage state="progress" />);
    expect(container.querySelector('[data-region="actionbar"]')).toBeNull();
    expect(container.querySelector('[data-region="report-panel"]')).toBeNull();
  });
});

describe('05_多部位态 · 部位指示条的正面证据', () => {
  it('部位数 ≥ 2 → 指示条渲染，含二值计数与可点警示点', () => {
    const { container } = render(<WorkspacePage state="multi-organ" />);
    expect(container.querySelector('[data-region="organ-bar"]')).not.toBeNull();
    expect(screen.getByText('已扫 2 / 3 · 未扫 1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '需复核 1' })).toBeInTheDocument();
  });

  it('指示条只承载二值+计数；「需复核」是独立可点警示点', () => {
    const { container } = render(<WorkspacePage state="multi-organ" />);
    expect(container.querySelector('.ws-organ-bar__count')).not.toBeNull();
    expect(ruleBody(workspaceCss, '.ws-organ-bar__alert')).toMatch(
      /color:\s*var\(--color-attention\)/
    );
  });

  it('高 24、锚在影像区内部底缘（bottom:0），不改变影像区高度', () => {
    const { container } = render(<WorkspacePage state="multi-organ" />);
    expect(
      container.querySelector('[data-region="organ-bar"]')!.closest('[data-region="image"]')
    ).not.toBeNull();
    const body = ruleBody(workspaceCss, '.ws-organ-bar');
    expect(body).toMatch(/bottom:\s*0/);
    expect(body).toMatch(/height:\s*24px/);
  });

  it('副文案说明结论按部位分段给出', () => {
    render(<WorkspacePage state="multi-organ" />);
    expect(screen.getByText(/结论按部位分别给出/)).toBeInTheDocument();
  });
});

describe('06_停档禁忌命中 · 全稿唯一打断式提示', () => {
  it('红实心提示 + 规则原文，不静默阻断', () => {
    const { container } = render(<WorkspacePage state="stopped" />);
    expect(container.querySelector('.ws-stop-alert')).toBeInTheDocument();
    expect(screen.getByText('禁忌命中，不得签发')).toBeInTheDocument();
    expect(screen.getByText(/《禁忌规则》§3\.2/)).toBeInTheDocument();
    expect(ruleBody(workspaceCss, '.ws-stop-alert')).toMatch(
      /background:\s*var\(--color-semantic-stop\)/
    );
  });

  it('结论仍然可见（挡住的是签发，不是信息）', () => {
    render(<WorkspacePage state="stopped" />);
    expect(
      screen.getByText('考虑良性结节（TI-RADS 3），建议 6 个月后复查')
    ).toBeInTheDocument();
  });

  it('签发按钮禁用 + 前置说明', () => {
    render(<WorkspacePage state="stopped" />);
    expect(screen.getByText('需先处理禁忌命中')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '签发（已禁用）' })).toBeDisabled();
    expect(ruleBody(workspaceCss, '.ws-primary-button:disabled')).toMatch(
      /background:\s*var\(--color-border\)/
    );
  });

  it('按 P1 不渲染「看一眼」标记行与恶性风险行', () => {
    const { container } = render(<WorkspacePage state="stopped" />);
    expect(container.querySelector('.ws-attention-chip')).toBeNull();
    expect(container.querySelector('.ws-risk-row')).toBeNull();
  });

  it('「停」档是全稿唯一的红，不与橙/绿同屏', () => {
    const { container } = render(<WorkspacePage state="stopped" />);
    expect(container.innerHTML).not.toMatch(/#f59e0b|#10b981|#faad14|#52c41a/i);
  });
});

describe('07_报告侧面板 与 12_签发后', () => {
  it('07：面板渲染、标题「报告草稿」、状态行不渲染', () => {
    const { container } = render(<WorkspacePage state="report" />);
    expect(container.querySelector('[data-region="report-panel"]')).not.toBeNull();
    expect(screen.getByText('报告草稿')).toBeInTheDocument();
    expect(screen.queryByText(/报告状态：/)).toBeNull();
    // 2026-10-28 修（P0-1/P1-8）：签发按钮的可点条件变了 ——
    //   ① `onSign` 未传 → 禁用（原来是 onClick=undefined 却看起来可点）；
    //   ② 无真实正文（state=report 且未传 reportText）→ 禁用（无正文不能签）。
    // 这里断言"两个条件都如实反映在按钮上"，不再断言"默认就可用"（那是旧假象）。
    expect(screen.getByRole('button', { name: '签发' })).toBeDisabled();
    expect(container.querySelector('[data-region="report-empty"]')).not.toBeNull();
  });

  it('07：有真实正文 + 有 onSign 时签发才可用', () => {
    const { container } = render(
      <WorkspacePage
        state="report"
        reportText="所见：肝脏大小形态正常，实质回声均匀。"
        onSign={() => undefined}
      />
    );
    expect(screen.getByRole('button', { name: '签发' })).toBeEnabled();
    // 断言 scope 到**报告面板内部**：影像区的设计预览标注框里本来就有
    // 「18.4 × 12.1 mm」（那是 WorkspaceImageRegion 的画布示意，与本条无关）。
    const panel = container.querySelector('[data-region="report-panel"]')!;
    expect(panel.textContent).not.toMatch(/18\.4 × 12\.1 mm/);
    expect(panel.querySelector('[data-region="report-body"]')).not.toBeNull();
  });

  it('12：标题「已签发」、签发禁用、状态行出现', () => {
    const { container } = render(<WorkspacePage state="signed" />);
    // 「已签发」同时是面板标题与禁用按钮文案，按 class 精确定位标题
    expect(container.querySelector('.ws-report-panel__title')!.textContent).toBe('已签发');
    expect(screen.getByRole('button', { name: '已签发' })).toBeDisabled();
    expect(
      screen.getByText(/报告状态：已签发（HSM 数字签名）· 已推送 PACS/)
    ).toBeInTheDocument();
  });

  it('12：唯一主操作是「下一个病人」', () => {
    const onSignNext = vi.fn();
    render(<WorkspacePage state="signed" onSignNext={onSignNext} />);
    fireEvent.click(screen.getByRole('button', { name: '下一个病人' }));
    expect(onSignNext).toHaveBeenCalledTimes(1);
  });

  it('面板是 480×860 贴右边缘的滑出面板，不是独立页面', () => {
    const { container } = render(<WorkspacePage state="report" />);
    const body = ruleBody(workspaceCss, '.ws-report-panel');
    expect(body).toMatch(/width:\s*480px/);
    expect(body).toMatch(/height:\s*860px/);
    expect(body).toMatch(/right:\s*0/);
    expect(body).toMatch(/top:\s*40px/);
    expect(container.querySelector('[data-region="report-panel"]')).not.toBeNull();
  });

  it('无正文时只出空态，**不再渲染画布示意文字**（P1-8 反证腿）', () => {
    const { container } = render(<WorkspacePage state="report" />);
    // 修复前这里会渲染「18.4 × 12.1 mm 低回声结节」= 假超声所见。
    // 断言 scope 到面板内部（影像区自己的画布示意框是另一回事，不在本条范围）。
    const panel = container.querySelector('[data-region="report-panel"]')!;
    expect(panel.textContent).not.toMatch(/18\.4 × 12\.1 mm/);
    expect(panel.textContent).not.toMatch(/TI-RADS 3 类/);
    expect(panel.querySelector('[data-region="report-empty"]')).not.toBeNull();
  });

  it('无面板的状态下 DOM 里不存在面板', () => {
    for (const state of ['scanning', 'conclusion', 'stopped'] as const) {
      const { container, unmount } = render(<WorkspacePage state={state} />);
      expect(container.querySelector('[data-region="report-panel"]')).toBeNull();
      unmount();
    }
  });
});

describe('11_AI不可用态', () => {
  it('主文案用「看一眼」档（attention）而不是红色', () => {
    const { container } = render(<WorkspacePage state="ai-unavailable" />);
    const headline = container.querySelector('.ws-conclusion__headline')!;
    expect(headline.textContent).toBe('AI 辅助暂不可用');
    expect(headline.className).toContain('--attention');
    expect(ruleBody(workspaceCss, '.ws-conclusion__headline--attention')).toMatch(
      /color:\s*var\(--color-attention\)/
    );
  });

  it('visible:false 的两个节点不渲染（无复核理由、无恶性风险值）', () => {
    const { container } = render(<WorkspacePage state="ai-unavailable" />);
    expect(container.querySelector('.ws-attention-chip')).toBeNull();
    expect(container.querySelector('.ws-risk-row')).toBeNull();
    expect(container.querySelector('.ws-stop-alert')).toBeNull();
  });

  it('保住手工路径：AI 不可用不等于工作流中断', () => {
    render(<WorkspacePage state="ai-unavailable" />);
    expect(screen.getByText(/请完全依赖人工判断/)).toBeInTheDocument();
    expect(screen.getByText(/连接恢复后会自动重试/)).toBeInTheDocument();
  });
});

describe('13_诊室遮蔽态 / 14_多部位标签排', () => {
  it('13：遮蔽层出现时结论卡内容不渲染（P1）', () => {
    const { container } = render(<WorkspacePage state="masked" />);
    expect(container.querySelector('[data-region="conclusion-mask"]')).not.toBeNull();
    expect(screen.getByText('AI 结论已就绪')).toBeInTheDocument();
    expect(screen.queryByText('恶性风险')).toBeNull();
    expect(container.querySelector('.ws-risk-row')).toBeNull();
  });

  it('13：「显示结论」是医生显式动作，主文案不带语义色', () => {
    const onReveal = vi.fn();
    render(<WorkspacePage state="masked" onRevealConclusion={onReveal} />);
    fireEvent.click(screen.getByRole('button', { name: '显示结论' }));
    expect(onReveal).toHaveBeenCalledTimes(1);
    expect(ruleBody(workspaceCss, '.ws-conclusion-mask__title')).toMatch(
      /color:\s*var\(--color-text-secondary\)/
    );
  });

  it('14：标签排三态 + 当前部位结论，且不出现基准结论卡', () => {
    const { container } = render(<WorkspacePage state="organ-tags" />);
    expect(
      container.querySelector('[data-region="conclusion-organ-tags"]')
    ).not.toBeNull();
    expect(screen.getByText('甲状腺 · 已完成')).toBeInTheDocument();
    expect(screen.getByText('颈部淋巴结 · 进行中')).toBeInTheDocument();
    expect(screen.getByText('肝脏 · 待扫')).toBeInTheDocument();
    expect(screen.getByText(/当前部位（颈部淋巴结）/)).toBeInTheDocument();
    expect(container.querySelector('.ws-risk-row')).toBeNull();
  });

  it('14：三个标签态用形状/字重区分，不靠第三个语义色', () => {
    const { container } = render(<WorkspacePage state="organ-tags" />);
    const tags = container.querySelectorAll('.ws-organ-tag');
    expect(tags).toHaveLength(3);
    expect(tags[0].className).not.toContain('--active');
    expect(tags[0].className).not.toContain('--pending');
    expect(tags[1].className).toContain('--active');
    expect(tags[2].className).toContain('--pending');
  });

  it('14：同时具备指示条与标签排（双条件渲染的正面证据）', () => {
    const { container } = render(<WorkspacePage state="organ-tags" />);
    expect(container.querySelector('[data-region="organ-bar"]')).not.toBeNull();
    expect(
      container.querySelector('[data-region="conclusion-organ-tags"]')
    ).not.toBeNull();
  });

  it('单部位状态（02/04）下两个部位相关元素都不存在', () => {
    for (const state of ['scanning', 'conclusion'] as const) {
      const { container, unmount } = render(<WorkspacePage state={state} />);
      expect(container.querySelector('[data-region="organ-bar"]')).toBeNull();
      expect(
        container.querySelector('[data-region="conclusion-organ-tags"]')
      ).toBeNull();
      unmount();
    }
  });
});

describe('语义色两级（17-§2）', () => {
  it('全站不使用 antd success / warning 预设色值', () => {
    expect(workspaceCss).not.toMatch(/#52c41a|#faad14|#10b981|#f59e0b/);
  });

  it('「正常」不渲染任何颜色：04 判读态没有红色元素', () => {
    const { container } = render(<WorkspacePage state="conclusion" />);
    expect(container.querySelector('.ws-stop-alert')).toBeNull();
  });
});
