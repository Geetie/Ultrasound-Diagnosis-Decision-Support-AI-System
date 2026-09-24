import { describe, expect, it } from 'vitest';

import { getConsentDisplayText, isConsentGranted, isPureReportMode } from '../consentGuard';

describe('consentGuard（T-G1-INT-06 同意状态展示判定）', () => {
  it('仅在 granted 时放行 AI 展示', () => {
    expect(isConsentGranted('granted')).toBe(true);
    expect(isConsentGranted('denied')).toBe(false);
    expect(isConsentGranted('withdrawn')).toBe(false);
    expect(isConsentGranted('pending')).toBe(false);
    expect(isConsentGranted(null)).toBe(false);
  });

  it('撤回与未获取状态都进入纯报告模式', () => {
    expect(isPureReportMode('withdrawn')).toBe(true);
    expect(isPureReportMode('denied')).toBe(true);
    expect(isPureReportMode(null)).toBe(true);
  });

  it('审批中不算纯报告模式（尚未有结论可拦截）', () => {
    expect(isPureReportMode('pending')).toBe(false);
  });

  it('granted 不进入纯报告模式', () => {
    expect(isPureReportMode('granted')).toBe(false);
  });

  it('撤回文案明确告知患者已撤回且进入纯报告模式', () => {
    const text = getConsentDisplayText('withdrawn');

    expect(text).toContain('撤回');
    expect(text).toContain('纯报告模式');
  });

  it('未获取状态不谎报为已授权', () => {
    const text = getConsentDisplayText(null);

    expect(text).toContain('未获取');
  });
});
