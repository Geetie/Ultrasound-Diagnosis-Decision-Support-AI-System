import { describe, expect, it } from 'vitest';

import { T2A_REASON_LABELS, t2aPredictedLabel, t2aReasonLabel } from '../t2aLabels';

/**
 * 02-1 T2a 纯映射逻辑（零依赖，任何环境秒级可跑）。
 * 渲染层用例见 WorkspaceImageRegion.test.tsx（cornerstone 链，高负载环境
 * 可能 OOM/超慢 —— A.100.4 环境说明）。
 */
describe('t2aPredictedLabel', () => {
  it('封闭集合映射中文', () => {
    expect(t2aPredictedLabel('Benign')).toBe('良性');
    expect(t2aPredictedLabel('Malignant')).toBe('恶性');
    expect(t2aPredictedLabel('Normal')).toBe('正常');
  });

  it('未知值原样透传；缺省不猜', () => {
    expect(t2aPredictedLabel('Indeterminate')).toBe('Indeterminate');
    expect(t2aPredictedLabel(undefined)).toBe('类别未提供');
  });
});

describe('t2aReasonLabel', () => {
  it('封闭集合 reason 全量有中文映射（后端加 reason 必须同步这里）', () => {
    // 后端 reason 集合（_safe_t2a_classification docstring + t2a_classifier.py）
    const backendReasons = [
      'disabled',
      'no_image_ids',
      'image_not_indexed',
      'no_image_bytes',
      'frame_decode_failed',
      'service_unavailable',
      'model_missing',
      'invalid_response',
      'not_run',
    ];
    for (const r of backendReasons) {
      expect(t2aReasonLabel(r)).not.toBe('原因未提供');
      expect(T2A_REASON_LABELS[r]).toBeTruthy();
    }
  });

  it('未知 reason 兜底"原因未提供"，不编造具体原因', () => {
    expect(t2aReasonLabel('some_new_reason')).toBe('原因未提供');
    expect(t2aReasonLabel(undefined)).toBe('原因未提供');
  });
});
