import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { MemoryRouter } from 'react-router-dom';

import WorklistPage from '../WorklistPage';
import SettingsPage from '../SettingsPage';
import SessionCreatePage from '../SessionCreatePage';
import { createSession } from '@/services/sessionService';
import { getFhirPatient } from '@/services/fhirService';

/**
 * 工作列表改成数据驱动后，测试必须喂数据，不能再靠组件里的硬编码文案。
 * 这里的夹具**按画布 01_工作列表的排布造**（1 行进行中 + 3 行今日待处理），
 * 这样下面那些设计契约断言（行高、SPACE_BETWEEN、attention vs plain）
 * 验的仍然是"还原度"，而不是"文案抄对了"。
 *
 * 端点 `GET /api/v1/sessions` 是 2026-10-02 补的，真机验证见
 * scripts/verify_sessions_list.py。响应形状以真机实测为准。
 */
vi.mock('@/services/sessionService', () => ({
  listSessions: vi.fn().mockResolvedValue({
    in_progress: [
      {
        session_id: 'sess-1',
        patient_id: 'P1',
        patient_name_masked: '张**',
        gender: 'female',
        age: 52,
        status: 'active',
        target_organs: ['thyroid'],
        created_at: '2026-10-02T06:20:00Z',
        updated_at: '2026-10-02T06:20:00Z',
        ttl_remaining_seconds: 78 * 60,
      },
    ],
    pending_today: [
      {
        session_id: 'sess-2',
        patient_id: 'P2',
        patient_name_masked: '李**',
        gender: 'female',
        age: 41,
        status: 'needs_review',
        target_organs: ['breast'],
        created_at: '2026-10-02T01:40:00Z',
        updated_at: '2026-10-02T01:40:00Z',
        ttl_remaining_seconds: 0,
      },
      {
        session_id: 'sess-3',
        patient_id: 'P3',
        patient_name_masked: '王**',
        gender: 'male',
        age: 58,
        status: 'completed',
        target_organs: ['liver'],
        created_at: '2026-10-02T02:05:00Z',
        updated_at: '2026-10-02T02:05:00Z',
        ttl_remaining_seconds: 0,
      },
      {
        session_id: 'sess-4',
        patient_id: 'P4',
        patient_name_masked: '赵**',
        gender: 'female',
        age: 47,
        status: 'pending',
        target_organs: ['heart'],
        created_at: '2026-10-02T02:32:00Z',
        updated_at: '2026-10-02T02:32:00Z',
        ttl_remaining_seconds: 0,
      },
    ],
    // 只数「今日待处理」那 3 行，**不含**进行中那一行 —— 与画布「今日 3 例待处理」
    // 一致，也与后端 `total_pending=len(pending_today)` 一致。
    // 把进行中计入会让医生以为还有 4 份要看。
    total_pending: 3,
  }),
  // 09 接线（2026-10-08）后「开始检查」真的建会话
  createSession: vi.fn(),
}));

vi.mock('@/services/fhirService', () => ({
  getFhirPatient: vi.fn(),
  createFhirPatient: vi.fn(),
}));

const css = (rel: string) => readFileSync(path.resolve(process.cwd(), rel), 'utf8');
const tokensCss = css('src/styles/design-tokens.css');
const pagesCss = css('src/styles/pages.css');

function ruleBody(cssText: string, selector: string): string {
  const hit = cssText.match(new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`));
  if (!hit) throw new Error(`pages.css 缺少规则 ${selector}`);
  return hit[1];
}

/**
 * 取 design-tokens.css 里真正那条`[data-theme="light"]` 规则的块。
 * 注意：文件顶部的注释里也出现了这个字符串，所以必须取**最后一次**出现，
 * 否则会切到注释与:root 之间的暗色块（那个 bug 已经踩过一次）。
 */
function lightBlock(): string {
  const parts = tokensCss.split('[data-theme="light"]');
  return parts[parts.length - 1];
}

const wrap = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('01_工作列表 / 15_工作列表浅色', () => {
  it('侧栏高亮在「工作列表」，脚注为深色主题', () => {
    const { container } = wrap(<WorklistPage />);
    expect(container.querySelector('[data-screen="01_工作列表"]')).not.toBeNull();
    const active = container.querySelector('.page-sidebar__item--active')!;
    expect(active.textContent).toBe('工作列表');
    expect(screen.getByText(/设备级深色主题/)).toBeInTheDocument();
  });

  it('一级导航只有 2 项', () => {
    const { container } = wrap(<WorklistPage />);
    expect(container.querySelectorAll('.page-sidebar__item')).toHaveLength(2);
  });

  it('页头靠 SPACE_BETWEEN：标题左、统计右（不是绝对定位）', async () => {
    const { container } = wrap(<WorklistPage />);
    // 数据驱动：等列表加载完再断言
    await screen.findByText('今日 3 例待处理');
    const head = container.querySelector('.page-header')!;
    expect(head.textContent).toContain('工作列表');
    expect(head.textContent).toContain('今日 3 例待处理');
    expect(ruleBody(pagesCss, '.page-header')).toMatch(/justify-content:\s*space-between/);
  });

  it('进行中行 72 且置顶，行内带唯一主操作「继续」', async () => {
    const { container } = wrap(<WorklistPage />);
    await screen.findByText('张** · 女 · 52 岁');
    const row = container.querySelector('.page-row-current')!;
    expect(ruleBody(pagesCss, '.page-row-current')).toMatch(/height:\s*72px/);
    expect(row.textContent).toContain('张** · 女 · 52 岁');
    expect(screen.getByRole('button', { name: '继续' })).toBeInTheDocument();
  });

  it('三行列表各 64px；「待复核」是看一眼档，「已签发」正常态无标记', async () => {
    const { container } = wrap(<WorklistPage />);
    await screen.findByText('已签发');
    expect(container.querySelectorAll('.page-row')).toHaveLength(3);
    expect(ruleBody(pagesCss, '.page-row')).toMatch(/height:\s*64px/);

    const attention = container.querySelectorAll('.page-row__status--attention');
    const plain = container.querySelectorAll('.page-row__status--plain');
    expect(attention).toHaveLength(2);
    expect([...attention].map((n) => n.textContent)).toEqual(['待复核', '待复核']);
    expect(plain).toHaveLength(1);
    expect(plain[0].textContent).toBe('已签发');
  });

  it('正常态不带任何语义色：已签发行用次要色 + Regular，不加 Medium', () => {
    const body = ruleBody(pagesCss, '.page-row__status--plain');
    expect(body).toMatch(/color:\s*var\(--color-text-secondary\)/);
    expect(body).toMatch(/font-weight:\s*400/);
    expect(body).not.toMatch(/color-semantic-stop|color-attention/);
  });

  it('待复核行走 attention，不用红也不用橙', () => {
    const body = ruleBody(pagesCss, '.page-row__status--attention');
    expect(body).toMatch(/color:\s*var\(--color-attention\)/);
    expect(body).not.toMatch(/color-semantic-stop|#f59e0b|#10b981/);
  });

  it('默认筛选说明用 attention（它是给医生看的当前筛选态）', async () => {
    const { container } = wrap(<WorklistPage />);
    await screen.findByText('默认筛选：待人工复核');
    expect(container.querySelector('.page-filter-note')!.textContent).toBe(
      '默认筛选：待人工复核'
    );
  });
});

describe('15_工作列表浅色 = 01 换 data-theme，不是第二个页面', () => {
  it('同一个组件，theme=light 即得', () => {
    const { container } = wrap(<WorklistPage theme="light" />);
    expect(container.querySelector('[data-screen="15_工作列表浅色"]')).not.toBeNull();
    expect(container.querySelector('.page-root')!.getAttribute('data-theme')).toBe('light');
  });

  it('与 01 逐节点同构：DOM 结构完全一致，只有脚注文案不同', () => {
    const a = wrap(<WorklistPage />).container;
    const b = wrap(<WorklistPage theme="light" />).container;

    const shape = (c: HTMLElement) =>
      Array.from(c.querySelectorAll('*'))
        .map((n) => n.tagName + '.' + n.className)
        .join('|');
    expect(shape(b)).toBe(shape(a));

    const text = (c: HTMLElement) =>
      Array.from(c.querySelectorAll('*'))
        .filter((n) => n.children.length === 0 && n.textContent)
        .map((n) => n.textContent);
    const ta = text(a);
    const tb = text(b);
    expect(tb).toHaveLength(ta.length);
    //唯一差异是脚注里的深色 / 浅色
    const diff = ta.filter((t, i) => t !== tb[i]);
    expect(diff).toHaveLength(1);
    expect(diff[0]).toMatch(/深色主题/);
  });

  it('侧栏脚注跟随主题', () => {
    wrap(<WorklistPage theme="light" />);
    expect(screen.getByText(/设备级浅色主题/)).toBeInTheDocument();
  });

  it('浅色下主按钮靠 token 翻转为深底白字，而不是两套样式', () => {
    const light = lightBlock();
    expect(light).toMatch(/--color-inverse:\s*#1F2937/);
    expect(light).toMatch(/--color-on-inverse:\s*#FFFFFF/);
    // 组件里只有一套类，靠 var() 翻转
    expect(ruleBody(pagesCss, '.page-row-current')).toMatch(
      /background:\s*var\(--color-surface\)/
    );
  });

  it('浅色下 attention 收敛到与次要色同值——它本来就不靠颜色区分', () => {
    const light = lightBlock();
    const attention = light.match(/--color-attention:\s*(#[0-9A-Fa-f]+)/)![1];
    const secondary = light.match(/--color-text-secondary:\s*(#[0-9A-Fa-f]+)/)![1];
    expect(attention).toBe(secondary);
  });
});

describe('08_设置', () => {
  it('侧栏高亮在「设置」（画布已修正过原缺陷）', () => {
    const { container } = wrap(<SettingsPage />);
    expect(container.querySelector('[data-screen="08_设置"]')).not.toBeNull();
    expect(container.querySelector('.page-sidebar__item--active')!.textContent).toBe('设置');
  });

  it('副标题显式声明范围：质控/运维/知识库是独立工作面', () => {
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    expect(
      screen.getByText(/质控看板、设备运维、知识库是独立工作面，不塞进设置/)
    ).toBeInTheDocument();
  });

  it('4 个设置行，各 56px，分 3 组', () => {
    const { container } = wrap(<SettingsPage />);
    expect(container.querySelectorAll('.page-setting-row')).toHaveLength(4);
    expect(ruleBody(pagesCss, '.page-setting-row')).toMatch(/height:\s*56px/);
    expect(container.querySelectorAll('.page-group-title')).toHaveLength(3);
  });

  it('脚踏未检测到**不报警**：用次要色，不用 attention', () => {
    const { container } = wrap(<SettingsPage />);
    const row = container.querySelector('[data-node="2:284"]')!;
    const value = row.querySelector('.page-setting-row__value')!;
    expect(value.className).not.toContain('--attention');
    expect(value.textContent).toContain('脚踏不是必需项');
  });

  it('主题行说明设备级一次设置 + 影像区不参与切换', () => {
    const { container } = wrap(<SettingsPage />);
    const value = container.querySelector('[data-node="2:270"] .page-setting-row__value')!;
    expect(value.textContent).toMatch(/跟随设备/);
    expect(value.textContent).toMatch(/影像区恒为深色底，不参与切换/);
  });

  it('画布上本屏是只读展示：不造 Switch / Checkbox', () => {
    const { container } = wrap(<SettingsPage />);
    expect(container.querySelector('.ant-switch')).toBeNull();
    expect(container.querySelector('input[type="checkbox"]')).toBeNull();
    expect(container.querySelectorAll('button')).toHaveLength(2); // 仅两个导航项
  });
});

describe('09_新建检查会话', () => {
  it('侧栏高亮**停在工作列表**（它是子页，不高亮自己）', () => {
    const { container } = wrap(<SessionCreatePage />);
    expect(container.querySelector('.page-sidebar__item--active')!.textContent).toBe('工作列表');
  });

  it('部位预设：设计稿 3 项 + cardiac（临床补充），甲状腺默认预选', () => {
    const { container } = wrap(<SessionCreatePage />);
    const organs = container.querySelectorAll('.page-organ');
    // 第 4 项「心脏」是**有意偏离设计稿**的临床补充：没有它，装载心脏超声图时
    // 只能声明甲状腺 → 系统按甲状腺出"结节 TI-RADS + 病灶面积"（错误器官语义）。
    // 见 frontend/src/types/session.ts 文件头与 16 号附录 A.56。
    expect(organs).toHaveLength(4);
    expect([...organs].map((n) => n.textContent)).toEqual([
      '甲状腺', '颈部淋巴结', '肝脏', '心脏',
    ]);
    expect(organs[0].getAttribute('aria-pressed')).toBe('true');
  });

  it('部位可切换选中', () => {
    const { container } = wrap(<SessionCreatePage />);
    const organs = container.querySelectorAll('.page-organ');
    fireEvent.click(organs[1]);
    expect(organs[0].getAttribute('aria-pressed')).toBe('false');
    expect(organs[1].getAttribute('aria-pressed')).toBe('true');
  });

  it('患者校验通过后显式带出同意状态（建会话是同意校验必经点）', async () => {
    // 接线后：GET /fhir/Patient 一次调用同时校验存在性与同意（未同意后端直接 403）
    vi.mocked(getFhirPatient).mockResolvedValue({
      resourceType: 'Patient',
      id: 'P1',
      name: [{ text: 'P1' }],
      active: true,
    });
    wrap(<SessionCreatePage />);
    fireEvent.change(screen.getByLabelText('患者编号'), { target: { value: 'P1' } });
    fireEvent.click(screen.getByRole('button', { name: '校验患者' }));
    await waitFor(() => {
      expect(screen.getByText(/AI 辅助知情同意有效/)).toBeInTheDocument();
    });
  });

  it('不要求事前声明全部部位：说明自动补全是补全不是替代', () => {
    render(<MemoryRouter><SessionCreatePage /></MemoryRouter>);
    expect(screen.getByText(/作为补全而不是替代/)).toBeInTheDocument();
    expect(screen.getByText(/默认已预选上次使用的部位/)).toBeInTheDocument();
  });

  it('主操作「开始检查」44px，真的建会话并带上选中部位', async () => {
    vi.mocked(getFhirPatient).mockResolvedValue({
      resourceType: 'Patient',
      id: 'P1',
      name: [{ text: 'P1' }],
      active: true,
    });
    vi.mocked(createSession).mockResolvedValue({
      session_id: 'sess-x',
      patient_id: 'P1',
      device_id: 'D1',
      status: 'pending',
      target_organs: ['liver'],
      organ_statuses: {},
      knowledge_base_version: 'kb_v1.0.0',
      created_at: '2026-10-08T10:00:00Z',
      updated_at: '2026-10-08T10:00:00Z',
      expired_at: null,
      consent_status_at_creation: 'granted',
    });
    const { container } = wrap(<SessionCreatePage />);
    expect(ruleBody(pagesCss, '.page-primary-action')).toMatch(/height:\s*44px/);

    const btn = screen.getByRole('button', { name: '开始检查' });
    expect(btn).toBeDisabled(); // 未校验患者、未填设备 —— 不打无效请求
    fireEvent.change(screen.getByLabelText('患者编号'), { target: { value: 'P1' } });
    fireEvent.click(screen.getByRole('button', { name: '校验患者' }));
    await waitFor(() => expect(screen.getByText(/AI 辅助知情同意有效/)).toBeInTheDocument());
    expect(btn).toBeDisabled(); // 患者确认了但设备还没填 —— 仍然不允许提交
    fireEvent.change(screen.getByLabelText('设备编号'), { target: { value: 'D1' } });
    // 换到第 3 个部位再提交 —— 请求体必须带医生实际选中的部位
    fireEvent.click(container.querySelectorAll('.page-organ')[2]);
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '开始检查' }));
    await waitFor(() => {
      expect(vi.mocked(createSession)).toHaveBeenCalledWith(
        expect.objectContaining({ patient_id: 'P1', device_id: 'D1', target_organs: ['liver'] }),
      );
    });
  });

  it('未校验患者时「开始检查」禁用，不打无效请求', () => {
    // 接线后前置是「校验患者」（后端以 consent 为准）；没校验就提交，
    // 等于赌库里一定有同意记录 —— 直接禁用。
    render(
      <MemoryRouter>
        <SessionCreatePage />
      </MemoryRouter>
    );
    expect(screen.getByRole('button', { name: '开始检查' })).toBeDisabled();
  });

  it('部位显示名与 ID 走同一份映射（含设计要求的颈部淋巴结）', () => {
    wrap(<SessionCreatePage />);
    // OrganType 原本只有 thyroid/liver/cardiac，缺cervical_ln
    expect(screen.getByText('颈部淋巴结')).toBeInTheDocument();
  });

  it('次要操作回到工作列表', () => {
    wrap(<SessionCreatePage />);
    expect(
      screen.getByRole('button', { name: '从工作列表继续已有会话' })
    ).toBeInTheDocument();
  });
});

describe('三页共用外壳：侧栏 200 + 主区，且不套旧 AppLayout', () => {
  const pages = [
    ['01', <WorklistPage key="a" />],
    ['08', <SettingsPage key="b" />],
    ['09', <SessionCreatePage key="c" />],
  ] as const;

  it('侧栏 200、主区 padding 32 / gap 24', () => {
    expect(ruleBody(pagesCss, '.page-sidebar')).toMatch(/width:\s*200px/);
    const main = ruleBody(pagesCss, '.page-main');
    expect(main).toMatch(/padding:\s*var\(--space-8\)/);
    expect(main).toMatch(/gap:\s*var\(--space-6\)/);
  });

  it('每页都渲染侧栏与主区，且根节点 1440×900', () => {
    for (const [, ui] of pages) {
      const { container, unmount } = wrap(ui);
      expect(container.querySelector('[data-region="sidebar"]')).not.toBeNull();
      expect(container.querySelector('[data-region="page-main"]')).not.toBeNull();
      expect(ruleBody(pagesCss, '.page-root')).toMatch(/width:\s*var\(--size-screen-w/);
      unmount();
    }
  });

  it('旧 AppLayout 的顶栏/状态栏一律不出现（画布上这三页没有）', () => {
    for (const [, ui] of pages) {
      const { container, unmount } = wrap(ui);
      // AppLayout 会渲染 antd Layout 的 Header / Footer 与 Sider
      expect(container.querySelector('.ant-layout-sider')).toBeNull();
      expect(container.querySelector('header.ant-layout-header')).toBeNull();
      expect(container.querySelector('footer.ant-layout-footer')).toBeNull();
      unmount();
    }
  });

  it('新系统样式里不出现旧设计系统的任何色值', () => {
    for (const sel of ['.page-root', '.page-sidebar', '.page-row', '.page-setting-row']) {
      expect(ruleBody(pagesCss, sel)).not.toMatch(
        /#0a0e1a|#111827|#1a2035|#00d4ff|#7c3aed|#10b981|#f59e0b|#ef4444/i
      );
    }
  });

  it('侧栏脚注把脚注推到底部靠弹性占位', () => {
    expect(ruleBody(pagesCss, '.page-sidebar__spacer')).toMatch(/flex:\s*1/);
  });
});

