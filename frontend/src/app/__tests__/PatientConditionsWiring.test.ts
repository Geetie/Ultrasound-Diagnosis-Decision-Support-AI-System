import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * 「医生勾选的禁忌 → 判读请求」链路的接线守卫（源码级）。
 *
 * 这条链路由四个文件共同构成：面板把中文标签存进 ClinicalInfoValue.conditions、
 * 类型层（api.ts）与 hook 入参（useWorkspaceFlow.ts）声明 conditions、
 * 路由层（routes.tsx）把标签换成规范码后塞进 patient_context，后端 SG 按码匹配。
 * 每一段单看都正常，合起来断掉时页面只表现为「勾了没反应」——
 * 2026-10 的实测就是：勾选值从未上行（类型里根本没有该字段），
 * 而且即便上行也是中文，SG 别名表里没有 → 命中数恒为 0（反向 fail-open）。
 *
 * 与 clinicalInfo.test.ts 分工：那边测纯函数，这边只钉「谁声明、谁转换、谁发送」。
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

describe('禁忌条件上行接线（C1）', () => {
  it('类型层声明了 patient_context.conditions', () => {
    const api = readSource('src/types/api.ts');

    // 只断言字段存在是不够的：此前"类型里没有 → TS 拦不住 → 运行时发不出去"。
    // 这里连注释里的三态语义一起钉住，避免有人把它改成必填或删掉。
    expect(api).toMatch(/patient_context\?:\s*\{[\s\S]*?conditions\?:\s*string\[\]/);
  });

  it('hook 的 start() 入参接受 conditions', () => {
    const hook = readSource('src/hooks/useWorkspaceFlow.ts');

    expect(hook).toMatch(/patientContext\?:\s*\{[\s\S]*?conditions\?:\s*string\[\]/);
    // 入参必须真的转进请求体，别只加类型不接线
    expect(hook).toContain('patient_context: input.patientContext');
  });

  it('路由层用 toConditionCodes() 转换后发送，而不是直接发 clinical.conditions', () => {
    const routes = readSource('src/app/routes.tsx');

    expect(routes).toContain('toConditionCodes(clinical.conditions)');
    // 反证：直接把中文标签数组塞进请求即红
    expect(routes).not.toMatch(/conditions:\s*clinical\.conditions/);
  });

  it('码表只有一处，且值是 ASCII 小写蛇形（中文标签当码发出即红）', () => {
    const info = readSource('src/components/workspace/clinicalInfo.ts');

    // 唯一来源：别处不得再自造一份映射
    const codeTableDecls = info.match(/CONDITION_CODES\s*=\s*\{/g) ?? [];
    expect(codeTableDecls.length).toBe(1);
    expect(info).toContain('export function toConditionCodes');

    // 从码表字面量里抽值，逐个钉形状。把 '妊娠' 写成值 → 下面立即失败。
    const table = info.match(/CONDITION_CODES\s*=\s*\{([\s\S]*?)\}\s*as const/);
    expect(table, 'CONDITION_CODES 声明被改了形状，请同步本断言').not.toBeNull();
    const values = [...table![1].matchAll(/'([^']+)'/g)]
      .map((m) => m[1])
      // 每行形如 `'妊娠': 'pregnancy'`，取冒号后的那个（值）
      .filter((_, i) => i % 2 === 1);
    expect(values.length).toBe(3);
    for (const code of values) {
      expect(code, `码「${code}」必须是 ASCII 小写蛇形`).toMatch(/^[a-z][a-z0-9_]*$/);
    }
    expect(values).toEqual(['pregnancy', 'contrast_allergy', 'anticoagulant_therapy']);
  });

  it('未勾选时送 []（不是 undefined）：转换函数不吃 undefined 输入也不吐 undefined', () => {
    const info = readSource('src/components/workspace/clinicalInfo.ts');

    // 函数签名吃 readonly string[]，空数组返回 [] —— 由函数体保证，注释写明语义
    expect(info).toMatch(/export function toConditionCodes\(labels: readonly string\[\]\): string\[\]/);
    expect(info).toContain('return codes');
    // 语义说明必须留在代码里（这条决定"每次判读是否都要人工复核"）
    expect(info).toMatch(/已确认无条件/);
    expect(info).toMatch(/缺数据即判命中/);
  });
});
