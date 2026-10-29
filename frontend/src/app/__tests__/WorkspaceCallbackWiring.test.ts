import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * 「回调必须真的接上」的接线守卫（源码级）—— 2026-10-28 界面修复的防回退腿。
 *
 * ## 为什么需要它（这轮修的真实缺陷）
 *
 * 2026-10-28 的界面盘点发现：工作区有 **3 个回调从来没有任何调用方传过** ——
 *
 *   · `WorkspaceReportPanel` 的 `onSign`（报告面板「签发」）
 *   · `WorkspaceReportPanel` 的 `onStructuredProofread`（「结构化校对」）
 *   · `WorkspaceConclusionBody` 的 `onSignNext`（签发后「下一个病人」）
 *
 * 三个按钮在页面上**长得完全正常**（不禁用、不变灰、有 hover），但 `onClick={undefined}`
 * —— 医生点了没反应，还以为是自己手抖。这正是"渲染级单测全绿、真机用户点不动"的典型：
 * 组件测的是"按钮在不在"，没人测"父组件有没有把回调传下来"。
 *
 * 本文件只钉一件事：**`routes.tsx` 必须把这三个回调传给 `WorkspacePage`**。
 * 它不锁实现细节（不关心回调里做了什么），只锁"传了没有"。
 *
 * ⚠️ 反向自测：把 `routes.tsx` 里任意一行的 `onXxx={...}` 删掉，本文件必须变红。
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

/**
 * 剥掉注释后再做「源码里不许出现某字面量」的断言（A.28.6 原则 1：守卫扫源码前必须剥注释）。
 *
 * 为什么必须剥：本轮修完 P1-8 后，`WorkspaceReportPanel.tsx` 的**文件头注释**里
 * 仍在解释"原来那段 18.4 × 12.1 mm 是假的、已被删除" —— 不剥注释的守卫会把
 * **说明文字**当成违规命中，判红一次之后就再也没人信它了。
 *
 * 逐字符状态机而不是正则：正则分不清 `//` 在字符串里还是真注释。
 */
function stripComments(source: string): string {
  let out = '';
  let i = 0;
  const n = source.length;
  let state: 'code' | 'line' | 'block' | 'single' | 'double' | 'template' = 'code';
  while (i < n) {
    const c = source[i]!;
    const next = source[i + 1];
    if (state === 'code') {
      if (c === '/' && next === '/') {
        state = 'line';
        i += 2;
        continue;
      }
      if (c === '/' && next === '*') {
        state = 'block';
        i += 2;
        continue;
      }
      if (c === "'") state = 'single';
      else if (c === '"') state = 'double';
      else if (c === '`') state = 'template';
      out += c;
      i += 1;
      continue;
    }
    if (state === 'line') {
      if (c === '\n') {
        state = 'code';
        out += c;
      }
      i += 1;
      continue;
    }
    if (state === 'block') {
      if (c === '*' && next === '/') {
        state = 'code';
        i += 2;
        continue;
      }
      i += 1;
      continue;
    }
    // 字符串 / 模板串内部：原样保留（含转义）
    out += c;
    if (c === '\\') {
      out += source[i + 1] ?? '';
      i += 2;
      continue;
    }
    if (
      (state === 'single' && c === "'") ||
      (state === 'double' && c === '"') ||
      (state === 'template' && c === '`')
    ) {
      state = 'code';
    }
    i += 1;
  }
  return out;
}

/** 读源码并剥注释 —— 所有"不许出现某字面量"的断言都必须用它。 */
function readCode(relative: string): string {
  return stripComments(readSource(relative));
}

describe('工作区三个回调必须由路由层传入（2026-10-28 修复的防回退腿）', () => {
  const routes = readSource('src/app/routes.tsx');

  /**
   * 断言"某属性以**传值形式**出现在 JSX 里"。
   * 只认 `prop={...}`，不认注释里提到的同名串 —— 这轮踩过的坑就是：
   * 注释里写着「`onSign` 由路由层从未传」，grep 到 onSign 却找不到真正的赋值。
   */
  function assertPropPassed(prop: string): void {
    const passed = new RegExp(`\\b${prop}=\\{`).test(routes);
    expect(passed, `routes.tsx 必须把 ${prop} 传给 WorkspacePage（否则按钮 onClick=undefined）`).toBe(
      true,
    );
  }

  it('onSign（报告面板「签发」）已传入', () => {
    assertPropPassed('onSign');
  });

  it('onStructuredProofread（「结构化校对」）已传入', () => {
    assertPropPassed('onStructuredProofread');
  });

  it('onSignNext（签发后「下一个病人」）已传入', () => {
    assertPropPassed('onSignNext');
  });

  it('readback（确认动作区回读文案）已传入 —— 否则回退到写死的 TI-RADS 3', () => {
    assertPropPassed('readback');
  });

  it('三个回调都不是空壳：必须指向真实实现（函数/表达式，不能是字面量 null）', () => {
    for (const prop of ['onSign', 'onStructuredProofread', 'onSignNext']) {
      const m = routes.match(new RegExp(`\\b${prop}=\\{([^}]*)\\}`));
      expect(m, `${prop} 应有 {…} 形式的传值`).not.toBeNull();
      const value = (m![1] ?? '').trim();
      expect(value.length, `${prop} 的传值不能为空`).toBeGreaterThan(0);
      expect(value, `${prop} 不能传 null/undefined（那等于没传）`).not.toMatch(
        /^(null|undefined)$/,
      );
    }
  });

  it('签发按钮真的会发请求：routes.tsx 调用 reportService.signReport', () => {
    // 只传回调但回调是空的，也还是"点了没反应"。钉住它真的调了签发接口。
    expect(routes).toMatch(/from '@\/services\/reportService'/);
    expect(routes).toMatch(/\bsignReport\s*\(/);
  });
});

describe('报告面板不得再渲染画布示意文字（P1-8 的源码级反证）', () => {
  const panel = readCode('src/components/workspace/WorkspaceReportPanel.tsx');

  it('组件源码里不再出现「18.4 × 12.1 mm」这类假超声所见', () => {
    expect(panel).not.toMatch(/18\.4\s*×\s*12\.1/);
    expect(panel).not.toMatch(/TI-RADS 3 类/);
    expect(panel).not.toMatch(/甲状腺右叶中极/);
  });

  it('无正文时走 report-empty 分支（空态），不是画布预览分支', () => {
    expect(panel).toMatch(/data-region="report-empty"/);
  });
});

describe('确认动作区回读文案不得写死分级（P0-5 的源码级反证）', () => {
  const bar = readCode('src/components/workspace/WorkspaceActionBar.tsx');

  it('源码里不再出现写死的「TI-RADS 3，考虑良性可能大」', () => {
    expect(bar).not.toMatch(/TI-RADS 3，考虑良性可能大/);
  });

  it('回读文案来自 readback 参数', () => {
    expect(bar).toMatch(/readback/);
  });
});

describe('建会话页支持多部位（P1-9 的源码级反证）', () => {
  const page = readCode('src/pages/SessionCreatePage.tsx');

  it('target_organs 不再是单元素数组字面量 [organ]', () => {
    expect(page).not.toMatch(/target_organs:\s*\[organ\]/);
  });

  it('存在多选状态（organs 数组）+ 切换函数', () => {
    expect(page).toMatch(/useState<OrganType\[\]>/);
    expect(page).toMatch(/toggleOrgan/);
  });
});
