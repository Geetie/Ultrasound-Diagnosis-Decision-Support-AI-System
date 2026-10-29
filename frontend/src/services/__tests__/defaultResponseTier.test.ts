import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { DEFAULT_RESPONSE_TIER, resolveStage } from '../diagnosisService';

/**
 * 02-3 前端档位收敛守卫（B1 批次）。
 *
 * 契约：'standard' 字面量收敛为单一来源 DEFAULT_RESPONSE_TIER；
 * 默认行为不静默改变（仍 standard）；ExaminationPage 提供医生可选的 full 入口。
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

describe('DEFAULT_RESPONSE_TIER 单一来源（02-3）', () => {
  it('默认仍是 standard —— 默认行为不得静默改变', () => {
    expect(DEFAULT_RESPONSE_TIER).toBe('standard');
  });

  it('resolveStage 未传 tier 时按 standard 档出阶段计划（默认不变）', () => {
    expect(resolveStage(0)).toEqual(resolveStage(0, 'standard'));
  });

  it('useWorkspaceFlow 的 tier 兜底全部走 DEFAULT_RESPONSE_TIER，不再散落字面量', () => {
    const source = readSource('src/hooks/useWorkspaceFlow.ts');
    expect(source).toContain('DEFAULT_RESPONSE_TIER');
    expect(source).not.toMatch(/\?\?\s*'standard'/);
  });

  it('diagnosisService 的默认参数同样收敛', () => {
    const source = readSource('src/services/diagnosisService.ts');
    expect(source).toContain("export const DEFAULT_RESPONSE_TIER = 'standard' as const;");
    expect(source).not.toMatch(/=\s*'standard'\s*[,;)]/);
  });

  it('ExaminationPage 提供医生可选档位入口（standard/full）', () => {
    const source = readSource('src/pages/ExaminationPage.tsx');
    expect(source).toContain('DEFAULT_RESPONSE_TIER');
    expect(source).toContain('value: \'full\'');
    expect(source).toMatch(/response_tier:\s*responseTier/);
    // 不得再硬编码
    expect(source).not.toContain("response_tier: 'standard'");
  });
});
