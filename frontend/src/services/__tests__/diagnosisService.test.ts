import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DiagnosisResult } from '@/types/api';

const getMock = vi.fn();

vi.mock('@/services/api', () => ({
  default: {
    get: (...args: unknown[]) => getMock(...args),
    post: vi.fn(),
  },
}));

import {
  extractStageTimings,
  pollDiagnosisResult,
  resolveStage,
  STAGE_LABELS,
} from '../diagnosisService';

function makeResult(overrides: Partial<DiagnosisResult> = {}): DiagnosisResult {
  return {
    request_id: 'req_1',
    status: 'completed',
    consistency_flag: 'consistent',
    desensitization_status: 'full',
    consent_status_at_output: 'granted',
    fusion_status: 'normal',
    diagnosis: {},
    evidence: {},
    quality_control: {},
    requires_manual_review: false,
    review_reason: null,
    ai_diagnosis_failed: false,
    hallucination_severity: null,
    version_compatibility: 'compatible',
    processing_time_ms: 1200,
    model_version: 'v1',
    knowledge_base_version: 'kb_v1.0.0',
    ...overrides,
  };
}

describe('resolveStage（预计阶段）', () => {
  it('档位不同时间线不同', () => {
    expect(resolveStage(100, 'realtime').key).toBe('submitted');
    expect(resolveStage(1000, 'realtime').key).toBe('retrieval');
    expect(resolveStage(1000, 'full').key).toBe('submitted');
    expect(resolveStage(3000, 'full').key).toBe('retrieval');
  });

  it('始终标注为预计（后端未提供实时进度）', () => {
    expect(resolveStage(5000, 'standard').estimated).toBe(true);
  });

  it('标签来自统一映射', () => {
    expect(resolveStage(0, 'standard').label).toBe(STAGE_LABELS.submitted);
  });

  it('超出时间线停留在最后阶段', () => {
    expect(resolveStage(10 ** 6, 'standard').key).toBe('finalizing');
  });
});

describe('extractStageTimings（真实阶段耗时）', () => {
  it('从 findings.timings_ms 提取各阶段与总耗时', () => {
    const result = makeResult({
      diagnosis: {
        timings_ms: {
          // 键名取自真机实测（2026-10-02），后端写的是 dispatcher.invoke_ms。
          // 曾经这里写 'dispatch'，测试用同一个错键造数据 → 一直绿，
          // 而真实响应里 retrieval 阶段永远取不到值。
          'dispatcher.invoke_ms': 800,
          'fusion.fuse_ms': 300,
          'sg.check_output_ms': 50,
          'pipeline.total_ms': 1500,
        },
      },
    });
    const timings = extractStageTimings(result);
    expect(timings.map((item) => item.key)).toEqual([
      'retrieval',
      'fusion',
      'safety',
      'finalizing',
    ]);
    expect(timings[0].durationMs).toBe(800);
    expect(timings[3].durationMs).toBe(1500);
  });

  it('无 timings 时返回空数组（不展示假数据）', () => {
    expect(extractStageTimings(makeResult())).toEqual([]);
    expect(extractStageTimings(null)).toEqual([]);
  });

  /**
   * 防回归：用**真机实测**的完整 timings_ms 键集跑一遍。
   * 这 5 个键来自 req_20261002_da4fa542 的真实响应；任何改名都必须同步改这里，
   * 否则会退化成「某阶段静默消失」——那正是 dispatch 键名错配造成过的故障。
   */
  it('真机实测键集下三个阶段全部命中（防键名错配回归）', () => {
    const result = makeResult({
      diagnosis: {
        timings_ms: {
          db_read_ms: 5.52,
          'fusion.fuse_ms': 656.17,
          'pipeline.total_ms': 2488.23,
          'sg.check_output_ms': 14.62,
          'dispatcher.invoke_ms': 1808.66,
        },
      },
    });
    const byKey = Object.fromEntries(
      extractStageTimings(result).map((t) => [t.key, t.durationMs])
    );
    expect(byKey.retrieval).toBe(1809);
    expect(byKey.fusion).toBe(656);
    expect(byKey.safety).toBe(15);
    expect(byKey.finalizing).toBe(2488);
  });
});

describe('pollDiagnosisResult（渐进式）', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    getMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('阶段按耗时推进，且不重复回调同一阶段', async () => {
    getMock.mockResolvedValue({ data: makeResult({ status: 'processing' }) });
    const stages: string[] = [];
    const onResult = vi.fn();

    const poller = pollDiagnosisResult('req_1', onResult, vi.fn(), {
      firstPollDelay: 100,
      pollInterval: 4000,
      maxWaitMs: 30000,
      tier: 'standard',
      onStage: (stage) => stages.push(stage.key),
    });

    await vi.advanceTimersByTimeAsync(110);
    await vi.advanceTimersByTimeAsync(5000);
    poller.cancel();

    expect(stages[0]).toBe('submitted');
    expect(new Set(stages).size).toBe(stages.length);
    expect(onResult).not.toHaveBeenCalled();
  });

  it('完成后发出真实阶段的 finalizing 并回调结果', async () => {
    const result = makeResult();
    getMock.mockResolvedValue({ data: result });
    const stages: Array<{ key: string; estimated: boolean }> = [];
    const onResult = vi.fn();

    pollDiagnosisResult('req_1', onResult, vi.fn(), {
      firstPollDelay: 100,
      pollInterval: 500,
      tier: 'standard',
      onStage: (stage) => stages.push({ key: stage.key, estimated: stage.estimated }),
    });

    await vi.advanceTimersByTimeAsync(200);

    expect(onResult).toHaveBeenCalledWith(result);
    expect(stages.at(-1)).toEqual({ key: 'finalizing', estimated: false });
  });

  it('超时回调且不再轮询', async () => {
    getMock.mockResolvedValue({ data: makeResult({ status: 'processing' }) });
    const onTimeout = vi.fn();

    pollDiagnosisResult('req_1', vi.fn(), onTimeout, {
      firstPollDelay: 100,
      pollInterval: 200,
      maxWaitMs: 1000,
    });

    await vi.advanceTimersByTimeAsync(2000);
    expect(onTimeout).toHaveBeenCalledTimes(1);
  });

  it('取消后不再回调（组件卸载场景）', async () => {
    getMock.mockResolvedValue({ data: makeResult({ status: 'processing' }) });
    const onResult = vi.fn();
    const onTimeout = vi.fn();

    const poller = pollDiagnosisResult('req_1', onResult, onTimeout, {
      firstPollDelay: 100,
      pollInterval: 200,
      maxWaitMs: 1000,
    });
    poller.cancel();

    await vi.advanceTimersByTimeAsync(3000);
    expect(onResult).not.toHaveBeenCalled();
    expect(onTimeout).not.toHaveBeenCalled();
  });

  it('检索失败时退避但仍会拿到最终结果', async () => {
    getMock
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValue({ data: makeResult() });
    const onResult = vi.fn();

    pollDiagnosisResult('req_1', onResult, vi.fn(), {
      firstPollDelay: 50,
      pollInterval: 100,
      maxBackoffMs: 1000,
      tier: 'realtime',
    });

    await vi.advanceTimersByTimeAsync(500);
    expect(onResult).toHaveBeenCalled();
  });
});
