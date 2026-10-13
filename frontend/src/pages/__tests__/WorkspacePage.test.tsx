import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import WorkspacePage from '../WorkspacePage';

// 直接读样式源文件，让断言能顺着「元素类名 → CSS 规则 → token 值」把骨架尺寸钉死，
// 绕开 jsdom 不做布局（getBoundingClientRect 恒为 0）的限制。
// 注意两点：① 不能用 `?raw`——本项目 vitest 未开启 css 处理，?raw 返回空串；
//② 不能用 import.meta.url 拼路径——jsdom 环境下它不是 file: 协议。
const css = (rel: string) => readFileSync(path.resolve(process.cwd(), rel), 'utf8');
const tokensCss = css('src/styles/design-tokens.css');
const workspaceCss = css('src/styles/workspace.css');

/** 从 design-tokens.css 读一个 px 尺寸 token 的数值。 */
function tokenPx(name: string): number {
  const hit = tokensCss.match(new RegExp(`${name}:\\s*(\\d+)px`));
  if (!hit) throw new Error(`design-tokens.css 缺少 ${name}`);
  return Number(hit[1]);
}

function ruleBody(css: string, selector: string): string {
  const hit = css.match(new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`));
  if (!hit) throw new Error(`workspace.css 缺少规则 ${selector}`);
  return hit[1];
}

describe('WorkspacePage · 02_扫查态骨架（design_data 2:1）', () => {
  it('渲染通栏三区，顺序为 常驻条 → 影像区 → 结论区', () => {
    const { container } = render(<WorkspacePage />);

    const bar = container.querySelector('[data-region="bar"]');
    const image = container.querySelector('[data-region="image"]');
    const conclusion = container.querySelector('[data-region="conclusion"]');

    expect(bar).not.toBeNull();
    expect(image).not.toBeNull();
    expect(conclusion).not.toBeNull();
    expect(
      bar!.compareDocumentPosition(image!) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
    expect(
      image!.compareDocumentPosition(conclusion!) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });

  it('影像区高度恒定 600，且由 token 驱动而非硬编码', () => {
    expect(tokenPx('--size-image-h')).toBe(600);
    expect(ruleBody(workspaceCss, '.ws-image')).toMatch(/height:\s*var\(--size-image-h\)/);
    // 恒定＝不参与 flex 收缩，任何状态下都不能被压小
    expect(ruleBody(workspaceCss, '.ws-image')).toMatch(/flex-shrink:\s*0/);
  });

  it('基准骨架合计恰好 900（40 + 600 + 260）', () => {
    expect(tokenPx('--size-bar-h')).toBe(40);
    expect(tokenPx('--size-conclusion-h')).toBe(260);
    expect(
      tokenPx('--size-bar-h') + tokenPx('--size-image-h') + tokenPx('--size-conclusion-h')
    ).toBe(900);
  });

  it('确认动作区出现时结论区收至 200，两条路径都恰好 900', () => {
    expect(tokenPx('--size-actionbar-h')).toBe(60);
    expect(tokenPx('--size-conclusion-h-compact')).toBe(200);
    expect(
      tokenPx('--size-bar-h') +
        tokenPx('--size-image-h') +
        tokenPx('--size-conclusion-h-compact') +
        tokenPx('--size-actionbar-h')
    ).toBe(900);
  });

  it('结论区高度取自 size-conclusion-h', () => {
    expect(ruleBody(workspaceCss, '.ws-conclusion')).toMatch(
      /height:\s*var\(--size-conclusion-h\)/
    );
  });
});

describe('WorkspacePage · 契约§1「按规则不渲染」= DOM 里不存在', () => {
  it('无待确认项 → 不渲染确认动作区', () => {
    const { container } = render(<WorkspacePage />);
    expect(container.querySelector('[data-region="actionbar"]')).toBeNull();
    expect(container.querySelector('[data-region="organ-bar"]')).toBeNull();
  });

  it('同意状态 / 连接状态正常时一律不渲染（默认沉默）', () => {
    render(<WorkspacePage />);
    expect(screen.queryByText(/同意/)).toBeNull();
    expect(screen.queryByText(/连接/)).toBeNull();
  });

  it('单部位 → 不渲染部位指示条与部位标签排', () => {
    const { container } = render(<WorkspacePage />);
    expect(container.querySelector('[data-node="2:216"]')).toBeNull();
    expect(screen.queryByText(/已扫.*未扫/)).toBeNull();
  });

  it('草稿态 → 不渲染报告状态行', () => {
    render(<WorkspacePage />);
    expect(screen.queryByText(/报告草稿|已签发/)).toBeNull();
  });

  it('画布上的评阅用标注（区域编号 1~4）不实现', () => {
    render(<WorkspacePage />);
    for (const label of ['1 常驻条', '2 影像区', '3 结论区']) {
      expect(screen.queryByText(label)).toBeNull();
    }
    expect(screen.queryByText(/4 确认动作区/)).toBeNull();
  });

  it('正常态不渲染任何语义色：全稿无绿、无橙', () => {
    const { container } = render(<WorkspacePage />);
    // 两级语义色之外的旧系统色一个都不许进场
    expect(workspaceCss).not.toMatch(/#10b981|#f59e0b|#00d4ff|#7c3aed|#0a0e1a|#111827/);
    expect(container.querySelector('[style*="rgb(16, 185, 129)"]')).toBeNull();
  });
});

describe('WorkspacePage · 02 扫查态元素还原', () => {
  it('常驻条身份由数据驱动（2:3；写死人名 = 患者身份错显，A.36 P0）', () => {
    render(<WorkspacePage identity="张** · 女 · 52 岁" />);
    expect(screen.getByText('张** · 女 · 52 岁')).toBeInTheDocument();
  });

  it('结论区空态双文案（2:8 / 2:9）', () => {
    render(<WorkspacePage />);
    // 文案偏离画布一处：原文「等待冻结取图」预设设备端冻结键，浏览器部署里没有这个键
    expect(screen.getByText('等待装载影像')).toBeInTheDocument();
    expect(
      screen.getByText('装载当前帧即自动发起判读，不需要点击任何按钮')
    ).toBeInTheDocument();
  });

  it('影像区角标数据驱动：部位 / 参数 / 时间一律不写死（A.57）', () => {
    // 无声明部位、无会话时间 → 如实说"没有"，不编。旧实现写死
    // 「甲状腺 · 线阵 12 MHz · 增益 62 · 深度 10 cm」与「2026-10-02 14:26:41」，
    // 于是**心脏会话也显示甲状腺 + 线阵探头参数**（真机 p26 走查实证），
    // 属 A.36「身份写死」同类问题：占位值被当成了真实检查信息。
    render(<WorkspacePage />);
    expect(screen.getByText('部位未声明 · 参数待装载')).toBeInTheDocument();
    expect(screen.getByText('时间未提供')).toBeInTheDocument();
  });

  it('影像区角标用声明部位 + 会话起始时间（心脏会话不再显示甲状腺）', () => {
    render(<WorkspacePage declaredOrgan="心脏" sessionStartedAt="2026-10-13T03:59:56.000Z" />);
    expect(screen.getByText('心脏 · 参数待装载')).toBeInTheDocument();
    // 本地时区无关：只断格式（YYYY-MM-DD HH:mm:ss）
    expect(screen.getByText(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)).toBeInTheDocument();
  });

  it('影像区角标与测量值按节点坐标还原', () => {
    const { container } = render(<WorkspacePage />);

    expect(screen.getByText('18.4 × 12.1 mm')).toBeInTheDocument();
    expect(screen.getByText('结节 A · 未校验')).toBeInTheDocument();
    expect(screen.getByText('结节 B · 已校验')).toBeInTheDocument();

    const scale = container.querySelector('[data-node="2:100"]');
    expect(scale?.textContent).toBe('0 cm\n2 cm\n4 cm\n6 cm\n8 cm\n10 cm');
  });

  it('校验态靠线型区分，不靠颜色（2:5 虚线 / 2:6 实线）', () => {
    const { container } = render(<WorkspacePage />);

    expect(container.querySelector('[data-node="2:5"]')?.className).toContain(
      'ws-anno--unverified'
    );
    expect(container.querySelector('[data-node="2:6"]')?.className).toContain(
      'ws-anno--verified'
    );
    // A.55：标注框压在影像底板上 → 用**不随主题漂移**的叠加 token
    // （此前用 --color-attention：浅色主题下 #5A6673 对 #15171B 只有 3.06，几乎看不见）
    expect(ruleBody(workspaceCss, '.ws-anno--unverified')).toMatch(
      /border:\s*2px dashed var\(--color-overlay-attention\)/
    );
    expect(ruleBody(workspaceCss, '.ws-anno--verified')).toMatch(
      /border:\s*2px solid var\(--color-overlay-text\)/
    );
  });

  it('影像区底色用 color-image-bg，且不随主题切换', () => {
    expect(ruleBody(workspaceCss, '.ws-image')).toMatch(
      /background-color:\s*var\(--color-image-bg\)/
    );
    // 注意：文件头部注释里也出现 [data-theme="light"]，必须取最后一次出现
    const parts = tokensCss.split('[data-theme="light"]');
    const light = parts[parts.length - 1];
    expect(light).toMatch(/--color-image-bg:\s*#15171B/);
  });

  it('叠加标注压在真实超声上要有可读性底板（对比度 ≥ AA 正文 4.5）', () => {
    // 依据 AGENT_TASKS §九.1「叠加标注与真实组织纹理的对比度从未验证」。
    // 实测 US-365K真图：不加底板时 attention 标签 1.63、primary 标签 2.55、
    // 角标 2.23、测量值 2.38，均不达 AA 大字 3.0；加不透明底板后全部 ≥ 6。
    for (const sel of ['.ws-image__text', '.ws-measure__value']) {
      expect(ruleBody(workspaceCss, sel)).toMatch(/background:\s*var\(--color-image-bg\)/);
    }
    // 底板必须极紧，不许遮挡解剖结构
    expect(ruleBody(workspaceCss, '.ws-image__text')).toMatch(/padding:\s*2px 4px/);
  });

  it('深度刻度落在扇区外纯黑区，不加底板（实测底纹 0,0,0，对比度 9.39）', () => {
    expect(ruleBody(workspaceCss, '.ws-image__scale')).not.toMatch(/background:/);
  });

  it('角标文字禁止折行（数据驱动文案长度不受控）', () => {
    // A.57 之前这条的理由是「原文时间角标有两个空格」，而 nowrap 会折叠连续空格。
    // 时间角标改数据驱动（formatLocalTimestamp，单空格）后该理由消失，但**禁止折行**
    // 仍然必要：部位名/参数文案来自数据，折行会顶破 600 骨架里的角标位置。
    expect(ruleBody(workspaceCss, '.ws-image__text')).toMatch(/white-space:\s*pre\b/);
    expect(ruleBody(workspaceCss, '.ws-image__text')).not.toMatch(/white-space:\s*nowrap/);
  });

  it('深度刻度是多行文本，用 pre-line 而非 pre', () => {
    expect(ruleBody(workspaceCss, '.ws-image__scale')).toMatch(/white-space:\s*pre-line/);
  });
});

describe('WorkspacePage · 04_判读态结论（design_data 2:12）', () => {
  it('结论区内容顶左对齐，不使用空态的居中修饰符', () => {
    const { container } = render(<WorkspacePage state="conclusion" />);
    const conclusion = container.querySelector('[data-region="conclusion"]')!;
    expect(conclusion.className).not.toContain('--centered');
    // 04 的 2:18 是 MIN/MIN；空态 02 的 2:7 才是 CENTER
    expect(ruleBody(workspaceCss, '.ws-conclusion')).toMatch(/align-items:\s*flex-start/);
    expect(ruleBody(workspaceCss, '.ws-conclusion--centered')).toMatch(
      /align-items:\s*center/
    );
  });

  it('结论区一级三样：一句话结论 + 恶性风险 + 建议动作', () => {
    render(<WorkspacePage state="conclusion" />);
    expect(
      screen.getByText('考虑良性结节（TI-RADS 3），建议 6 个月后复查')
    ).toBeInTheDocument();
    expect(screen.getByText('恶性风险')).toBeInTheDocument();
    expect(screen.getByText('低 · 约 2%–5%')).toBeInTheDocument();
    expect(
      screen.getByText('建议：6 个月后复查超声，暂不需穿刺')
    ).toBeInTheDocument();
  });

  it('恶性风险必须带区间与措辞，不给裸百分比', () => {
    const { container } = render(<WorkspacePage state="conclusion" />);
    const value = container.querySelector('.ws-risk-row__value')!;
    expect(value.textContent).toMatch(/约\s*\d+%–\d+%/);
  });

  it('「看一眼」档＝青灰描边 + 图标 + 人话化文案，静态不发声', () => {
    const { container } = render(<WorkspacePage state="conclusion" />);
    const chip = container.querySelector('.ws-attention-chip')!;
    expect(chip).toBeInTheDocument();
    expect(
      screen.getByText('AI 建议人工复核 · 置信度低于典型阈值')
    ).toBeInTheDocument();
    expect(ruleBody(workspaceCss, '.ws-attention-chip')).toMatch(
      /border:\s*1\.5px solid var\(--color-attention\)/
    );
    // 「看一眼」是静态的：不得有 animation / transition
    expect(ruleBody(workspaceCss, '.ws-attention-chip')).not.toMatch(/animation|transition/);
  });

  it('图标用 currentColor 跟主题，不烘焙色值', () => {
    const { container } = render(<WorkspacePage state="conclusion" />);
    const svg = container.querySelector('.ws-attention-chip__icon')!;
    expect(svg).toBeInTheDocument();
    const markup = svg.outerHTML;
    expect(markup).toContain('currentColor');
    expect(markup).not.toMatch(/#8C99A6|#FFF/i);
  });

  it('展开入口全屏唯一（P2 一次决策：不拆成三个并列按钮）', () => {
    const { container } = render(<WorkspacePage state="conclusion" />);
    const entries = container.querySelectorAll('[data-action="expand"]');
    expect(entries).toHaveLength(1);
    expect(entries[0].textContent).toBe('展开依据 / 鉴别诊断 / 诊断过程');
    // 不允许出现"展开依据""鉴别诊断""诊断过程"三个独立按钮
    expect(container.querySelectorAll('button')).toHaveLength(0);
  });

  it('判读态同样不渲染确认动作区与部位指示条', () => {
    const { container } = render(<WorkspacePage state="conclusion" />);
    expect(container.querySelector('[data-region="actionbar"]')).toBeNull();
    expect(container.querySelector('[data-region="organ-bar"]')).toBeNull();
  });

  it('影像区在判读态仍是 600（不因结论区内容变多而缩放）', () => {
    const { container } = render(<WorkspacePage state="conclusion" />);
    expect(container.querySelector('[data-region="image"]')).not.toBeNull();
    expect(ruleBody(workspaceCss, '.ws-image')).toMatch(/height:\s*var\(--size-image-h\)/);
    expect(ruleBody(workspaceCss, '.ws-image')).toMatch(/flex-shrink:\s*0/);
  });

  it('两态复用同一个影像区组件，不重复实现', () => {
    const { container: a } = render(<WorkspacePage state="scanning" />);
    const { container: b } = render(<WorkspacePage state="conclusion" />);
    expect(a.querySelectorAll('[data-region="image"]')).toHaveLength(1);
    expect(b.querySelectorAll('[data-region="image"]')).toHaveLength(1);
    // 叠加层子节点数一致
    expect(
      a.querySelectorAll('.ws-image__overlay > *').length
    ).toBe(b.querySelectorAll('.ws-image__overlay > *').length);
  });
});

describe('WorkspacePage · 10_确认动作区（design_data 2:297）', () => {
  it('有动作区时结论区收至 200，其余状态仍260', () => {
    const { container: withBar } = render(<WorkspacePage state="actionbar" />);
    expect(
      withBar.querySelector('[data-region="conclusion"]')!.className
    ).toContain('ws-conclusion--compact');

    const { container: without } = render(<WorkspacePage state="conclusion" />);
    expect(
      without.querySelector('[data-region="conclusion"]')!.className
    ).not.toContain('ws-conclusion--compact');

    expect(tokenPx('--size-conclusion-h-compact')).toBe(200);
    expect(ruleBody(workspaceCss, '.ws-conclusion--compact')).toMatch(
      /height:\s*var\(--size-conclusion-h-compact\)/
    );
  });

  it('动作区高度 60，且布局为 SPACE_BETWEEN + 居中', () => {
    const { container } = render(<WorkspacePage state="actionbar" />);
    const bar = container.querySelector('[data-region="actionbar"]')!;
    expect(bar).toBeInTheDocument();

    expect(tokenPx('--size-actionbar-h')).toBe(60);
    const body = ruleBody(workspaceCss, '.ws-actionbar');
    expect(body).toMatch(/height:\s*var\(--size-actionbar-h\)/);
    expect(body).toMatch(/justify-content:\s*space-between/);
    expect(body).toMatch(/align-items:\s*center/);
  });

  it('被压缩的是结论区，影像区仍是 600', () => {
    const { container } = render(<WorkspacePage state="actionbar" />);
    expect(container.querySelector('[data-region="image"]')).not.toBeNull();
    expect(ruleBody(workspaceCss, '.ws-image')).toMatch(/height:\s*var\(--size-image-h\)/);
    expect(ruleBody(workspaceCss, '.ws-image')).toMatch(/flex-shrink:\s*0/);
  });

  it('无待确认项的两个状态里，动作区根本不在 DOM 中', () => {
    for (const state of ['scanning', 'conclusion'] as const) {
      const { container } = render(<WorkspacePage state={state} />);
      expect(container.querySelector('[data-region="actionbar"]')).toBeNull();
    }
  });

  it('回读文案 + 等效说明 + 唯一确认按钮', () => {
    render(<WorkspacePage state="actionbar" />);
    expect(
      screen.getByText('已回读：TI-RADS 3，考虑良性可能大 —— 请给一个确认动作')
    ).toBeInTheDocument();
    // 脚踏不得为必需项：等效说明必须在
    expect(screen.getByText(/三选一等效/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认结论' })).toBeInTheDocument();
  });

  it('P2：动作区只有一个待选动作，不出现并列第二选择', () => {
    render(<WorkspacePage state="actionbar" />);
    expect(screen.getAllByRole('button')).toHaveLength(1);
  });

  it('确认按钮点击回调被触发', () => {
    const onConfirm = vi.fn();
    render(<WorkspacePage state="actionbar" onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole('button', { name: '确认结论' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('主按钮用 color-inverse / color-on-inverse，不是品牌色也不是 antd primary', () => {
    const body = ruleBody(workspaceCss, '.ws-primary-button');
    expect(body).toMatch(/background:\s*var\(--color-inverse\)/);
    expect(body).toMatch(/color:\s*var\(--color-on-inverse\)/);
    expect(body).not.toMatch(/#1677ff|#1890ff|#00d4ff|#7c3aed/i);
  });

  it('确认动作区同样不出现绿色', () => {
    const { container } = render(<WorkspacePage state="actionbar" />);
    expect(workspaceCss).not.toMatch(/#10b981|#f59e0b/);
    expect(container.querySelector('[style*="rgb(16, 185, 129)"]')).toBeNull();
  });

  it('三态高度账都恰好 900', () => {
    const bar = tokenPx('--size-bar-h');
    const image = tokenPx('--size-image-h');
    const action = tokenPx('--size-actionbar-h');
    // 无动作区
    expect(bar + image + tokenPx('--size-conclusion-h')).toBe(900);
    // 有动作区
    expect(bar + image + tokenPx('--size-conclusion-h-compact') + action).toBe(900);
  });
});
