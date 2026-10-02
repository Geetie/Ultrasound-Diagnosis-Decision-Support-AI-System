import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * T-G1-INT-06 接线守卫。
 *
 * 背景：consentGuard.ts 里的判定函数此前**全仓无任何 import/调用**——守卫写好
 * 了却没人用，等于没有拦截。项目里已不止一次出现"定义了但没人接线"的缺陷
 * （指标无写入方、契约字段恒为占位值），故此处直接对页面源码做断言，防止
 * 后续改动把接线摘掉而测试仍然全绿。
 */
// 不用 import.meta.url：vitest 下它不是 file: 协议，readFileSync 会直接报
// "The URL must be of scheme file"。相对仓库根做兜底，避免 cwd 变化导致 ENOENT。
function _readPageSource(): string {
  const candidates = [
    resolve(process.cwd(), 'src/pages/DiagnosisPage.tsx'),
    resolve(process.cwd(), 'frontend/src/pages/DiagnosisPage.tsx'),
  ];
  const found = candidates.find((path) => existsSync(path));
  if (!found) {
    throw new Error(`未找到 DiagnosisPage.tsx，已尝试：${candidates.join(', ')}`);
  }
  return readFileSync(found, 'utf-8');
}

const pageSource = _readPageSource();

describe('DiagnosisPage 同意状态拦截接线', () => {
  it('导入了同意守卫', () => {
    expect(pageSource).toContain('isPureReportMode');
    expect(pageSource).toContain('getConsentDisplayText');
    expect(pageSource).toContain("from '@/utils/consentGuard'");
  });

  it('实际消费了守卫结果而非只导入', () => {
    expect(pageSource).toMatch(/isPureReportMode\(result\.consent_status_at_output\)/);
    expect(pageSource).toMatch(/getConsentDisplayText\(result\.consent_status_at_output\)/);
  });

  it('纯报告模式下整段拦截 AI 输出', () => {
    expect(pageSource).toContain('if (pureReportMode)');
    expect(pageSource).toContain('data-testid="consent-blocked-notice"');
  });

  it('拦截提示告知医生以原始报告为准', () => {
    expect(pageSource).toContain('AI 辅助诊断结论已按同意状态拦截');
  });
});
