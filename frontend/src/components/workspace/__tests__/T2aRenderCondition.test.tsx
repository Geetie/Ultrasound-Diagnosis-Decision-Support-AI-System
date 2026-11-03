/**
 * G12 / N3：T2a 芯片的渲染门槛必须是**后端字段可用性**，不能是"有没有影像栈"。
 *
 * 缺陷：`{t2a && stack.length > 0 && <T2aChip/>}` —— 无影像栈的会话
 * （纯文本判读、影像加载失败）即使后端已产出分类结果也不显示，
 * 医生看不到"未做影像分类"的真实原因。
 *
 * 本测试只钉渲染条件（源级断言），不引入 cornerstone 链。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const SRC = readFileSync(
  resolve(__dirname, '../WorkspaceImageRegion.tsx'),
  'utf8',
);

describe('T2a 渲染条件（G12/N3）', () => {
  it('不再要求 stack.length > 0', () => {
    expect(SRC).not.toMatch(/\{t2a\s*&&\s*stack\.length\s*>\s*0\s*&&\s*<T2aChip/);
  });

  it('仍然要求 t2a 存在才渲染（不凭空造芯片）', () => {
    expect(SRC).toMatch(/\{t2a\s*&&\s*<T2aChip/);
  });
});
