import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DiagnosisResult } from '@/types/api';
import {
  BLOCK_REVEAL_ORDER,
  useProgressiveDiagnosis,
} from '../useProgressiveDiagnosis';

function makeResult(diagnosis: Record<string, unknown> = {}): DiagnosisResult {
  return {
    request_id: 'req_1',
    status: 'completed',
    consistency_flag: 'consistent',
    desensitization_status: 'full',
    consent_status_at_output: 'granted',
    fusion_status: 'normal',
    diagnosis,
    evidence: {},
    quality_control: {},
    requires_manual_review: false,
    review_reason: null,
    ai_diagnosis_failed: false,
    hallucination_severity: null,
    version_compatibility: 'compatible',
    processing_time_ms: 100,
    model_version: 'v1',
    knowledge_base_version: 'kb_v1',
  };
}

describe('useProgressiveDiagnosis', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('结论先揭示，其余分块随后补齐', () => {
    const { result, rerender } = renderHook(
      ({ payload }: { payload: DiagnosisResult | null }) => useProgressiveDiagnosis(payload),
      { initialProps: { payload: null as DiagnosisResult | null } }
    );

    expect(result.current.allRevealed).toBe(false);

    rerender({ payload: makeResult() });

    // 结论（0ms）立即可见
    expect(result.current.isRevealed('conclusion')).toBe(true);
    expect(result.current.isRevealed('evidence')).toBe(false);

    act(() => {
      vi.advanceTimersByTime(500);
    });

    expect(result.current.isRevealed('hallucination')).toBe(true);
    expect(result.current.isRevealed('quality')).toBe(true);
    expect(result.current.isRevealed('evidence')).toBe(true);
    expect(result.current.allRevealed).toBe(true);
  });

  it('结果切回 null 时清空揭示状态（重新诊断场景）', () => {
    const { result, rerender } = renderHook(
      ({ payload }: { payload: DiagnosisResult | null }) => useProgressiveDiagnosis(payload),
      { initialProps: { payload: null as DiagnosisResult | null } }
    );

    rerender({ payload: makeResult() });
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(result.current.allRevealed).toBe(true);

    rerender({ payload: null });
    expect(result.current.revealed.size).toBe(0);
  });

  it('揭示顺序覆盖全部区块', () => {
    expect(BLOCK_REVEAL_ORDER).toEqual(['conclusion', 'hallucination', 'quality', 'evidence']);
  });

  it('真实阶段耗时随结果更新', () => {
    const { result, rerender } = renderHook(
      ({ payload }: { payload: DiagnosisResult | null }) => useProgressiveDiagnosis(payload),
      { initialProps: { payload: null as DiagnosisResult | null } }
    );

    expect(result.current.stageTimings).toEqual([]);

    rerender({ payload: makeResult({ timings_ms: { 'fusion.fuse_ms': 123 } }) });
    expect(result.current.stageTimings).toHaveLength(1);
    expect(result.current.stageTimings[0].durationMs).toBe(123);
  });
});
