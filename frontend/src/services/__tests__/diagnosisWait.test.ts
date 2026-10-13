import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import apiClient from '../api';
import { pollDiagnosisResult, RESULT_WAIT_MS } from '../diagnosisService';
import { useWorkspaceFlow } from '@/hooks/useWorkspaceFlow';

/**
 * 判读「等不到结果」这条链的回归守卫。
 *
 * A.28 真机走查发现的 P0：`pollDiagnosisResult` 的 `maxWaitMs` 默认曾是 **30s**，
 * 而真实管线要 90~300s，于是前端 30s 就放弃轮询；`timeout` 又被映射回 `progress`，
 * 界面**永远停在「分析中」** —— 医生永远拿不到结论，也永远走不到报告与签名。
 * 门禁 D 组全绿是因为它是**服务器侧**自己轮询的，用自己的 300s 预算。
 *
 * 这里钉三件事：上限必须与服务端阈值同量级、`timeout` 是终态、flow 交出 request_id。
 */

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

/** 构造最小可用的 DiagnosisResult 形状（其余字段本组用例不关心）。 */
function result(status: string) {
  return { request_id: 'req_1', status } as never;
}

describe('轮询总等待上限', () => {
  it('RESULT_WAIT_MS 必须大于服务端 result_read_timeout_seconds（默认 180s）', () => {
    // 服务端 app/config/settings.py: result_read_timeout_seconds = 180
    expect(RESULT_WAIT_MS).toBeGreaterThan(180_000);
  });

  it('不再是 30s —— 那个值小于真实管线耗时，是 A.28 的 P0 根因', () => {
    expect(RESULT_WAIT_MS).not.toBe(30_000);
  });
});

describe('pollDiagnosisResult · 终态判定', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(apiClient.get).mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('status=needs_review 也是终态：待复核的检查不许停在「分析中」（A.59 真机实测）', async () => {
    // 真机 p28 抓到的形态：判读已完成、只是要求人工复核（status=needs_review），
    // 而终态判断原先只认 completed/failed/timeout → 前端当"还在跑"，
    // 界面永远停在「分析中 已用时 N 秒」，医生看不到结论也走不到报告与签名。
    vi.mocked(apiClient.get).mockResolvedValue({ data: result('needs_review') });
    const onResult = vi.fn();
    const onTimeout = vi.fn();

    pollDiagnosisResult('req_1', onResult, onTimeout, {
      firstPollDelay: 10,
      pollInterval: 100,
      maxWaitMs: RESULT_WAIT_MS,
    });
    await vi.advanceTimersByTimeAsync(50);

    expect(onResult).toHaveBeenCalledTimes(1);
    expect((onResult.mock.calls[0][0] as { status: string }).status).toBe('needs_review');
    expect(onTimeout).not.toHaveBeenCalled();
  });

  it('status=cancelled 也是终态（服务端会给出该状态）', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: result('cancelled') });
    const onResult = vi.fn();
    const onTimeout = vi.fn();

    pollDiagnosisResult('req_1', onResult, onTimeout, {
      firstPollDelay: 10,
      pollInterval: 100,
      maxWaitMs: RESULT_WAIT_MS,
    });
    await vi.advanceTimersByTimeAsync(50);

    expect(onResult).toHaveBeenCalledTimes(1);
    expect(onTimeout).not.toHaveBeenCalled();
  });

  it('status=timeout 也算终态：立刻回调而不是继续轮询到上限', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: result('timeout') });
    const onResult = vi.fn();
    const onTimeout = vi.fn();

    pollDiagnosisResult('req_1', onResult, onTimeout, {
      firstPollDelay: 10,
      pollInterval: 100,
      maxWaitMs: RESULT_WAIT_MS,
    });
    await vi.advanceTimersByTimeAsync(50);

    expect(onResult).toHaveBeenCalledTimes(1);
    expect((onResult.mock.calls[0][0] as { status: string }).status).toBe('timeout');
    // 关键：不能走到 onTimeout —— 那会把界面留在「分析中」
    expect(onTimeout).not.toHaveBeenCalled();
  });

  it('processing 期间持续轮询 60s，不提前放弃（旧实现 30s 就停）', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: result('processing') });
    const onResult = vi.fn();
    const onTimeout = vi.fn();

    pollDiagnosisResult('req_1', onResult, onTimeout, {
      firstPollDelay: 10,
      pollInterval: 100,
      maxWaitMs: RESULT_WAIT_MS,
    });
    await vi.advanceTimersByTimeAsync(60_000);

    expect(vi.mocked(apiClient.get).mock.calls.length).toBeGreaterThan(100);
    expect(onResult).not.toHaveBeenCalled();
    expect(onTimeout).not.toHaveBeenCalled();
  });
});

describe('useWorkspaceFlow · 交出 request_id', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(apiClient.post).mockReset();
    vi.mocked(apiClient.get).mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('start() 之后 requestId 可用 —— 报告页靠它，缺了医生只能手敲 URL', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: { request_id: 'req_abc', session_id: 's1', status: 'processing' },
    });
    vi.mocked(apiClient.get).mockResolvedValue({ data: result('processing') });

    const { result: hook } = renderHook(() => useWorkspaceFlow());
    await act(async () => {
      await hook.current.start({
        sessionId: 's1',
        imageIds: ['img_1'],
        targetOrgan: 'thyroid',
      });
    });

    expect(hook.current.requestId).toBe('req_abc');
  });

  it('resume() 同样交出 request_id（语音控件那条路径）', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: result('processing') });

    const { result: hook } = renderHook(() => useWorkspaceFlow());
    act(() => {
      hook.current.resume('req_from_voice');
    });

    expect(hook.current.requestId).toBe('req_from_voice');
  });
});