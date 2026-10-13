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

/**
 * 阶段 → `diagnosis.timings_ms` 真实键名的映射。
 *
 * 键名来自**真机实测**（2026-10-02，req_20261002_da4fa542），不是从 OpenAPI 猜的。
 * 后端实际写入的 5 个键：
 *   db_read_ms · fusion.fuse_ms · pipeline.total_ms · sg.check_output_ms · dispatcher.invoke_ms
 *
 * ⚠️ 曾把 retrieval 映射成 `'dispatch'` —— 后端从不写这个键，
 * 于是该阶段恒被 `typeof value === 'number'` 过滤掉，**永远不显示**。
 * 这类"键名对不上"缺陷单测抓不到（没有真实响应），只能实测。
 */
const TIMING_KEY_MAP: Array<[DiagnosisStageKey, string]> = [
  ['retrieval', 'dispatcher.invoke_ms'],
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

/**
 * 前端轮询的总等待上限（毫秒）。
 *
 * ⚠️ 这个值**必须大于服务端 `result_read_timeout_seconds`（默认 180s）**，
 * 两者是耦合的：服务端在结果接口里把超过阈值的 processing 判成 `status="timeout"`，
 * 客户端要**先收到这个终态信号**才能如实呈现，而不是自己先放弃。
 *
 * A.28.9 的教训：这里的默认值曾是 **30_000**，而真实管线要 90~300s
 * （LLM 串行链 + 冷启动）。于是前端 30s 就 `onTimeout()` 放弃轮询，
 * 而 `timeout` 又被 `PHASE_TO_STATE` 映射回 `progress` ——
 * **界面永远停在「分析中」，医生永远拿不到结论，也永远走不到报告与签名。**
 * A.24.1 当时只把服务端阈值从 30s 抬到 180s，前端这一侧没跟着改，
 * 属于典型的「修了一边」；真浏览器走查才把它暴露出来
 * （门禁 D 组是**服务器侧**自己轮询的，用的是自己的 300s 预算，所以全绿）。
 *
 * 取210s =服务端 180s + 30s 余量，让终态由服务端给出。
 */
export const RESULT_WAIT_MS = 210_000;

/**
 * 结果接口的**终态**集合。
 *
 * ⚠️ `needs_review` 是**终态** —— 判读已经完成，只是要求人工复核（A.59 真机实测：
 * 它原先不在终态判断里，于是「待复核」的检查被前端当成"还在跑"，界面永远停在
 * 「分析中 已用时 N 秒」，医生既看不到结论也走不到报告与签名；与 A.28「timeout
 * 不当终态」是**同一族**缺陷）。`cancelled` 同理（服务端会给出该状态）。
 *
 * 判据：**服务端语义**为准（`/api/v1/diagnosis/result/{rid}` 的 `status`）。
 * 后端已终态的状态集合见 `scripts/audit/p27_grade_extraction.py` 的轮询条件。
 */
export const TERMINAL_RESULT_STATUSES = [
  'completed',
  'needs_review',
  'failed',
  'cancelled',
  // 服务端把"超过 result_read_timeout_seconds 仍 processing"标成 timeout
  'timeout',
] as const;

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
  /** processing 中间态带后端已生成字符数（A.43 流式进度）；没有则不调 */
  onProgress?: (chars: number) => void;
  /**
   * processing 中间态带**已通过段落级 SG** 的预览文本（A.46）；
   * SG 拒绝后不再回调（前端隐藏预览，终态由全文 SG 兜底）
   */
  onPreview?: (preview: string) => void;
  /** SG 拒绝段落级预览 —— 前端停止显示（临床红线：SG 前文本不上屏） */
  onPreviewRefused?: () => void;
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
    // A.38：2000→1000。热身后判读 4.4s，2s 粒度会让医生感知多等 0~2s；
    // 轮询本身是轻查询（读 DB 一行），1s 无压力。退避（maxBackoffMs）仍在，
    // 长时间等待时不会打爆接口。
    pollInterval = 1000,
    maxWaitMs = RESULT_WAIT_MS,
    onProgress,
    onPreview,
    onPreviewRefused,
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
        // 'timeout' 是**终态**：服务端在结果接口里把超阈值仍processing 的请求
        // 标成它。不当作终态的话，客户端会一直轮询到 maxWaitMs 才放弃，
        // 期间界面停在「分析中」—— 医生看到的是"还在跑"，而后台早已判超时。
        if (
          TERMINAL_RESULT_STATUSES.includes(
            result.status as (typeof TERMINAL_RESULT_STATUSES)[number],
          )
        ) {
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
        // processing 中间态：后端流式生成进度（已生成字符数）——有的话透传
        if (onProgress && typeof result.generation_progress_chars === 'number') {
          onProgress(result.generation_progress_chars);
        }
        // 安全预览（A.46）：只透传**已过段落级 SG** 的文本。空串 = SG 拒绝的
        // 明确信号（onPreviewRefused）—— 临床红线：未过 SG 的原始输出不上屏
        if (typeof result.generation_preview === 'string') {
          if (result.generation_preview === '') {
            onPreviewRefused?.();
          } else if (onPreview) {
            onPreview(result.generation_preview);
          }
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
