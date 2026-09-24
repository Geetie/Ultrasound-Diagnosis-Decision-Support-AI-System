import apiClient from './api';
import type { DiagnosisRequest, DiagnosisRequestResponse, DiagnosisResult } from '@/types/api';

export async function requestDiagnosis(data: DiagnosisRequest): Promise<DiagnosisRequestResponse> {
  const response = await apiClient.post<DiagnosisRequestResponse>('/diagnosis/request', data);
  return response.data;
}

export async function getDiagnosisResult(requestId: string): Promise<DiagnosisResult> {
  const response = await apiClient.get<DiagnosisResult>(`/diagnosis/result/${requestId}`);
  return response.data;
}

/* ============================================================
   渐进式加载（T-GB-FE-01）
   ============================================================ */

export type DiagnosisStageKey =
  | 'submitted'
  | 'retrieval'
  | 'reasoning'
  | 'fusion'
  | 'safety'
  | 'finalizing';

export interface DiagnosisStageEvent {
  key: DiagnosisStageKey;
  label: string;
  /**
   * true 表示该阶段由"档位期望耗时"推算而来（后端未提供实时进度）。
   * UI 必须如实标注为预计阶段，不得伪装成真实遥测。
   */
  estimated: boolean;
  elapsedMs: number;
}

export const STAGE_LABELS: Record<DiagnosisStageKey, string> = {
  submitted: '已提交请求',
  retrieval: '检索知识库与证据',
  reasoning: '专科模型推理中',
  fusion: '融合与一致性校验',
  safety: '安全网关校验',
  finalizing: '生成结论',
};

/** 各档位下各阶段的预计到达时刻（依据规格 P99 与实测典型值划分）。 */
export const STAGE_PLAN_MS: Record<'realtime' | 'standard' | 'full', Array<{ key: DiagnosisStageKey; atMs: number }>> = {
  realtime: [
    { key: 'submitted', atMs: 0 },
    { key: 'retrieval', atMs: 500 },
    { key: 'reasoning', atMs: 1200 },
    { key: 'fusion', atMs: 2200 },
    { key: 'safety', atMs: 3200 },
    { key: 'finalizing', atMs: 4200 },
  ],
  standard: [
    { key: 'submitted', atMs: 0 },
    { key: 'retrieval', atMs: 800 },
    { key: 'reasoning', atMs: 2500 },
    { key: 'fusion', atMs: 6000 },
    { key: 'safety', atMs: 8500 },
    { key: 'finalizing', atMs: 10500 },
  ],
  full: [
    { key: 'submitted', atMs: 0 },
    { key: 'retrieval', atMs: 1200 },
    { key: 'reasoning', atMs: 4000 },
    { key: 'fusion', atMs: 9000 },
    { key: 'safety', atMs: 13000 },
    { key: 'finalizing', atMs: 15500 },
  ],
};

/** 由已耗时推算当前阶段（纯函数，便于单测）。 */
export function resolveStage(
  elapsedMs: number,
  tier: 'realtime' | 'standard' | 'full' = 'standard'
): DiagnosisStageEvent {
  const plan = STAGE_PLAN_MS[tier] ?? STAGE_PLAN_MS.standard;
  let current = plan[0];
  for (const entry of plan) {
    if (elapsedMs >= entry.atMs) current = entry;
  }
  return {
    key: current.key,
    label: STAGE_LABELS[current.key],
    estimated: true,
    elapsedMs,
  };
}

/**
 * 完成后的**真实**阶段耗时（来自 findings.timings_ms，由后端管线写入）。
 * 与上面的"预计阶段"区分开：进入结论页后可展示真实数据。
 */
export interface StageTiming {
  key: DiagnosisStageKey;
  label: string;
  durationMs: number;
}

const TIMING_KEY_MAP: Array<[DiagnosisStageKey, string]> = [
  ['retrieval', 'dispatch'],
  ['fusion', 'fusion.fuse_ms'],
  ['safety', 'sg.check_output_ms'],
];

export function extractStageTimings(result: DiagnosisResult | null): StageTiming[] {
  const diagnosis = (result?.diagnosis ?? {}) as Record<string, unknown>;
  const timings = (diagnosis.timings_ms ?? {}) as Record<string, number>;
  const stages: StageTiming[] = [];
  for (const [key, timingKey] of TIMING_KEY_MAP) {
    const value = timings[timingKey];
    if (typeof value === 'number') {
      stages.push({ key, label: STAGE_LABELS[key], durationMs: Math.round(value) });
    }
  }
  const total = timings['pipeline.total_ms'];
  if (typeof total === 'number') {
    stages.push({ key: 'finalizing', label: '管线总耗时', durationMs: Math.round(total) });
  }
  return stages;
}

export interface PollOptions {
  signal?: AbortSignal;
  firstPollDelay?: number;
  pollInterval?: number;
  maxWaitMs?: number;
  maxBackoffMs?: number;
  /** 响应档位，决定预计阶段时间线 */
  tier?: 'realtime' | 'standard' | 'full';
  /** 阶段变化回调（仅在阶段切换时触发，避免高频重渲染） */
  onStage?: (stage: DiagnosisStageEvent) => void;
}

export function pollDiagnosisResult(
  requestId: string,
  onResult: (result: DiagnosisResult) => void,
  onTimeout: () => void,
  options: PollOptions = {}
): { cancel: () => void } {
  const {
    signal,
    firstPollDelay = 1000,
    pollInterval = 2000,
    maxWaitMs = 30000,
    maxBackoffMs = 8000,
    tier = 'standard',
    onStage,
  } = options;

  let cancelled = false;
  let currentDelay = pollInterval;
  let lastStageKey: DiagnosisStageKey | null = null;

  const cancel = () => {
    cancelled = true;
  };

  const checkCancelled = () => cancelled || signal?.aborted;

  const emitStage = (event: DiagnosisStageEvent) => {
    if (!onStage) return;
    if (event.key === lastStageKey) return;
    lastStageKey = event.key;
    onStage(event);
  };

  const sleep = (ms: number) => new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms);
    if (signal) {
      signal.addEventListener('abort', () => {
        clearTimeout(timer);
        resolve();
      }, { once: true });
    }
  });

  (async () => {
    await sleep(firstPollDelay);
    if (checkCancelled()) return;

    const startTime = Date.now();
    emitStage(resolveStage(0, tier));

    while (Date.now() - startTime < maxWaitMs) {
      if (checkCancelled()) return;

      const elapsed = Date.now() - startTime;
      emitStage(resolveStage(elapsed, tier));

      try {
        const result = await getDiagnosisResult(requestId);
        if (checkCancelled()) return;
        if (result.status === 'completed' || result.status === 'failed') {
          // 结果已到：阶段切换为真实（estimated=false），再由调用方分段揭示
          emitStage({
            key: 'finalizing',
            label: STAGE_LABELS.finalizing,
            estimated: false,
            elapsedMs: Date.now() - startTime,
          });
          onResult(result);
          return;
        }
        currentDelay = pollInterval;
      } catch {
        if (checkCancelled()) return;
        // 瞬时失败（网络抖动/后端重启）按退避重试，不打断医生等待
        currentDelay = Math.min(currentDelay * 1.5, maxBackoffMs);
      }

      await sleep(currentDelay);
    }

    if (!checkCancelled()) {
      onTimeout();
    }
  })();

  return { cancel };
}
