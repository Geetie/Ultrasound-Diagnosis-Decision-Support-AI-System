import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * T7 可达性守卫（源码级）。
 *
 * 守的是两条**跨文件**契约 —— 单看任何一个文件都正常，合起来断掉时页面上只表现为
 * "点了没反应"或"结论出不来"：
 *   1. 多器官端点必须在真实链路里被调用（此前 `frontend/src` 对它零引用，
 *      等于跨器官冲突检测/一致性那两条防线生产上从未被喂过）；
 *   2. 侧栏每个菜单 key 都必须有已注册路由（`/diagnosis` 死链就是这么来的）。
 */
function readSource(relative: string): string {
  const candidates = [
    resolve(process.cwd(), relative),
    resolve(process.cwd(), 'frontend', relative),
  ];
  const found = candidates.find((path) => existsSync(path));
  if (!found) {
    throw new Error(`未找到 ${relative}，已尝试：${candidates.join(', ')}`);
  }
  return readFileSync(found, 'utf-8');
}

describe('多器官端点前端接线（T-GB-AGT-01）', () => {
  it('service 层真的指向 /diagnosis/multi-organ', () => {
    const svc = readSource('src/services/diagnosisService.ts');
    expect(svc).toContain("'/diagnosis/multi-organ'");
    expect(svc).toContain('requestMultiOrganDiagnosis');
  });

  it('hook 暴露 startMultiOrgan，且走的是多器官 service', () => {
    const hook = readSource('src/hooks/useWorkspaceFlow.ts');
    expect(hook).toContain('requestMultiOrganDiagnosis');
    expect(hook).toMatch(/startMultiOrgan:\s*\(input:\s*\{/);
    // 返回值里必须交出去，否则路由拿不到
    expect(hook).toMatch(/return\s*\{[\s\S]*startMultiOrgan,/);
  });

  it('路由层按部位数分支：≥2 走多器官，否则单器官（不是二选一写死）', () => {
    const routes = readSource('src/app/routes.tsx');

    expect(routes).toContain('targetOrgans.length >= 2');
    expect(routes).toContain('startMultiOrganFlow({');
    // 单器官路径必须仍在（不能为了接多器官把单部位会话搞坏）
    expect(routes).toContain('targetOrgan: organ');
  });

  it('多部位结果与 cross_organ 被渲染出来（不是只取回不显示）', () => {
    const routes = readSource('src/app/routes.tsx');
    const panel = readSource('src/components/workspace/WorkspaceMultiOrganPanel.tsx');

    expect(routes).toContain('WorkspaceMultiOrganPanel');
    expect(routes).toContain('crossOrgan={flow.crossOrgan}');
    // 失败器官必须有重跑入口
    expect(panel).toContain('onRerunOrgan');
    expect(panel).toContain('单独重跑该部位');
    // 一致性只是提示，必须写明不替代医生
    expect(panel).toContain('不替代医生判断');
  });

  it('cross_organ 三态在展示层区分（ok 才能说已核对）', () => {
    const view = readSource('src/components/workspace/multiOrganView.ts');
    expect(view).toContain("block.status !== 'ok'");
    expect(view).toContain('insufficient_responses');
    expect(view).toContain('unavailable');
  });
});

describe('侧栏死链守卫（每个菜单 key 都必须有路由）', () => {
  it('Sidebar 不含 /diagnosis', () => {
    const sidebar = readSource('src/components/layout/Sidebar.tsx');
    expect(sidebar).not.toContain("'/diagnosis'");
  });

  it('menuItems 的每个 key 都能在 routes.tsx 里找到 path', () => {
    const sidebar = readSource('src/components/layout/Sidebar.tsx');
    const routes = readSource('src/app/routes.tsx');

    const keys = [...sidebar.matchAll(/\{\s*key:\s*'([^']+)'/g)].map((m) => m[1]);
    // 抓到 0 条说明正则/写法变了，守卫已失效 —— 必须失败而不是静默通过
    expect(keys.length).toBeGreaterThan(5);

    const missing = keys.filter((key) => !routes.includes(`path="${key}"`));
    expect(missing, `这些侧栏菜单项没有对应路由，点了会被 * 兜底弹回 /：${missing.join(', ')}`)
      .toEqual([]);
  });
});

describe('生产不可达状态的处置有据可查（states.ts + routes.tsx）', () => {
  it('遮蔽态保持 dev-only 且理由写在代码里', () => {
    const states = readSource('src/components/workspace/states.ts');
    expect(states).toContain('DEV_ONLY_STATES');
    expect(states).toContain('isDevOnlyState');
    // 遮蔽态：全仓没有真实隐私开关 → 不造开关
    expect(states).toMatch(/隐私/);
  });

  it('报告面板/签发后已接生产入口，且正文必须来自后端 rendered', () => {
    const routes = readSource('src/app/routes.tsx');
    const panel = readSource('src/components/workspace/WorkspaceReportPanel.tsx');

    // 入口：有 request_id 后常驻条出现「报告面板」，点了就地开面板
    expect(routes).toContain("key: 'report-panel'");
    expect(routes).toContain('openReportPanel(rid)');
    expect(routes).toContain('getReportByRequest');
    // signed 必须由后端 signature_present 驱动（不能前端猜）
    expect(routes).toContain('signature_present');
    // 面板必须支持真实正文，且无正文时不得回落到画布示意文字
    expect(panel).toContain('reportText');
    expect(panel).toContain('report-body');
  });

  it('停档态不造假入口：注释里写明终止端点缺失与语义边界', () => {
    const routes = readSource('src/app/routes.tsx');
    // 必须留下判断记录，否则下一位 Agent 会再来"顺手接上"
    expect(routes).toMatch(/停档（06）的真实入口/);
    expect(routes).toMatch(/没有独立终止端点|没有\*\*独立终止端点/);
    expect(routes).toMatch(/伪造/);
  });
});

describe('死页面已登记但未删除（AGENTS.md 口径）', () => {
  it('两个死页面文件仍在，且文件头写明已无路由与保留原因', () => {
    for (const rel of [
      'src/pages/DiagnosisPage.tsx',
      'src/pages/ExaminationPage.tsx',
    ]) {
      const src = readSource(rel);
      expect(src, `${rel} 文件头缺少"已无路由"登记`).toMatch(/已无路由的遗留页面/);
      expect(src, `${rel} 文件头缺少保留原因`).toMatch(/为什么保留而不删/);
    }
  });

  it('feature_list.json 里登记为待处置（planned），不是悄悄留着', () => {
    // feature_list.json 在**仓库根**，不在 frontend/ 下（vitest 的 cwd 是 frontend/），
    // 所以单独解析，不走 readSource 的 src/ 约定。
    const candidates = [
      resolve(process.cwd(), '../feature_list.json'),
      resolve(process.cwd(), 'feature_list.json'),
      resolve(process.cwd(), '../../feature_list.json'),
    ];
    const found = candidates.find((p) => existsSync(p));
    expect(found, `未找到 feature_list.json，已尝试：${candidates.join(', ')}`).toBeDefined();

    const doc = JSON.parse(readFileSync(found!, 'utf-8')) as {
      features: { id: string; status: string }[];
    };
    const entry = doc.features.find((f) => f.id === 't7-frontend-dead-pages');
    expect(entry, 'feature_list.json 缺少 t7-frontend-dead-pages 登记').toBeDefined();
    expect(entry!.status).toBe('planned');
  });
});
