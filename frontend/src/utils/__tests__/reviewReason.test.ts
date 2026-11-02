import { describe, expect, it } from 'vitest';

import { REVIEW_REASON_LABELS, reviewReasonLabel } from '../reviewReason';

describe('reviewReasonLabel（G24）', () => {
  it('空值/空白 → undefined（不渲染）', () => {
    expect(reviewReasonLabel(undefined)).toBeUndefined();
    expect(reviewReasonLabel(null)).toBeUndefined();
    expect(reviewReasonLabel('   ')).toBeUndefined();
  });

  it('每个登记码都有中文，且不含英文原码当正文', () => {
    for (const [code, label] of Object.entries(REVIEW_REASON_LABELS)) {
      expect(label.length).toBeGreaterThan(0);
      expect(label).not.toBe(code);
    }
  });

  it('熔断前缀带出状态', () => {
    expect(reviewReasonLabel('ai_safety_circuit_breaker_suspended')).toBe(
      'AI 安全熔断（suspended），结论需人工确认',
    );
  });

  it('未知码不猜语义但可追溯', () => {
    expect(reviewReasonLabel('brand_new_code')).toBe('需人工复核（原因码：brand_new_code）');
    expect(reviewReasonLabel('ai_safety_circuit_breaker_')).toContain('unknown');
  });
});
