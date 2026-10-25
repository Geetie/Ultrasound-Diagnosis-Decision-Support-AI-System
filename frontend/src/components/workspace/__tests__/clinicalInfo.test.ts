import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import {
  CONDITION_CODES,
  EMPTY_CLINICAL_INFO,
  toClinicalText,
  toConditionCodes,
} from '../clinicalInfo';

/**
 * 勾选标签 → 安全网关规范码（C1 的纯函数半环）。
 *
 * 背景：面板上的值是中文（'妊娠'/'造影剂过敏'/'抗凝治疗'），而 SG 的
 * `_CONDITION_ALIASES` 只认 pregnancy / contrast_allergy / anticoagulant_therapy，
 * `_match_condition` 做的是 `any(c in aliases for c in patient_conditions)` ——
 * 中文串永不命中。也就是说"能不能拦住禁忌"完全取决于这里有没有把标签换成码。
 */
describe('toConditionCodes：中文标签 → SG 规范码', () => {
  it('三个勾选项各自映射到 SG 唯一认的码', () => {
    expect(toConditionCodes(['妊娠'])).toEqual(['pregnancy']);
    expect(toConditionCodes(['造影剂过敏'])).toEqual(['contrast_allergy']);
    expect(toConditionCodes(['抗凝治疗'])).toEqual(['anticoagulant_therapy']);
  });

  it('多选保序、去重，不串码', () => {
    expect(toConditionCodes(['妊娠', '抗凝治疗', '造影剂过敏'])).toEqual([
      'pregnancy',
      'anticoagulant_therapy',
      'contrast_allergy',
    ]);
    expect(toConditionCodes(['妊娠', '妊娠'])).toEqual(['pregnancy']);
  });

  it('未知标签直接丢弃，不猜成别的码', () => {
    // 猜错的后果是命中"错误的禁忌"或"本该命中却没命中"，比丢掉更危险。
    expect(toConditionCodes(['哮喘'])).toEqual([]);
    expect(toConditionCodes(['妊娠', '哮喘', 'pregnancy'])).toEqual(['pregnancy']);
  });

  it('空输入返回 [] 而不是 undefined（= 已确认无条件，SG 正常评估）', () => {
    const empty = toConditionCodes([]);
    expect(empty).toEqual([]);
    expect(empty).not.toBeUndefined();
    expect(Array.isArray(empty)).toBe(true);
    // EMPTY_CLINICAL_INFO 就是"医生一项都没勾"的初始态 → 必须得到 []
    expect(toConditionCodes(EMPTY_CLINICAL_INFO.conditions)).toEqual([]);
  });

  it('码必须是 ASCII 小写蛇形（钉死"不得把中文标签当码发出"）', () => {
    for (const [label, code] of Object.entries(CONDITION_CODES)) {
      expect(label, '键是界面显示用的中文标签').toMatch(/[\u4e00-\u9fa5]/);
      expect(code, `${label} 的码必须是 ASCII 小写蛇形`).toMatch(/^[a-z][a-z0-9_]*$/);
      expect(code, `${label} 的码不得含非 ASCII 字符`).not.toMatch(/[^\x20-\x7e]/);
    }
    // 反证：把中文值原样当码传出去，会立刻违反上面的形状约束
    const codes = toConditionCodes(['妊娠', '造影剂过敏', '抗凝治疗']);
    for (const c of codes) expect(c).toMatch(/^[a-z][a-z0-9_]*$/);
  });

  it('映射的码与 SG 别名表一致（写死 SG 侧参考值，防止单方面改名）', () => {
    // 参考：safety_gateway/contraindication.py 的 _CONDITION_ALIASES 键名。
    // 这里刻意写死字符串而不是 import 后端 —— 前端跨不过去，手抄一层当作契约。
    expect(CONDITION_CODES['妊娠']).toBe('pregnancy');
    expect(CONDITION_CODES['造影剂过敏']).toBe('contrast_allergy');
    expect(CONDITION_CODES['抗凝治疗']).toBe('anticoagulant_therapy');
  });

  it('面板上每一个勾选项都在码表里（漏码即红）', () => {
    // 从源码抽 CONDITION_OPTIONS：新增中文勾选项却忘了补码 → 该选项永远发不出，
    // 表现为"勾了没反应"，正是这次要修的 fail-open 的同款症状。
    const candidates = [
      resolve(process.cwd(), 'src/components/workspace/ClinicalInfoPanel.tsx'),
      resolve(process.cwd(), 'frontend', 'src/components/workspace/ClinicalInfoPanel.tsx'),
    ];
    const found = candidates.find((p) => existsSync(p));
    if (!found) throw new Error(`未找到 ClinicalInfoPanel.tsx，已尝试：${candidates.join(', ')}`);
    const source = readFileSync(found, 'utf-8');

    const optionsLine = source.match(/const CONDITION_OPTIONS = \[([^\]]+)\]/);
    expect(optionsLine, 'CONDITION_OPTIONS 声明被改了形状，请同步本断言').not.toBeNull();
    const labels = [...optionsLine![1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
    expect(labels.length).toBeGreaterThan(0);

    for (const label of labels) {
      expect(Object.hasOwn(CONDITION_CODES, label), `勾选项「${label}」缺规范码`).toBe(true);
    }
  });
});

describe('既有临床文本拼接不受影响', () => {
  it('toClinicalText 仍只拼症状/病史/化验，不把条件混进正文', () => {
    expect(
      toClinicalText({
        symptoms: '颈部肿物',
        history: '',
        labs: 'TSH 正常',
        conditions: ['妊娠'],
      }),
    ).toBe('症状：颈部肿物\n化验：TSH 正常');
  });
});
