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

export interface PollOptions {
  signal?: AbortSignal;
  firstPollDelay?: number;
  pollInterval?: number;
  maxWaitMs?: number;
  maxBackoffMs?: number;
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
  } = options;

  let cancelled = false;
  let currentDelay = pollInterval;

  const cancel = () => {
    cancelled = true;
  };

  const checkCancelled = () => cancelled || signal?.aborted;

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

    while (Date.now() - startTime < maxWaitMs) {
      if (checkCancelled()) return;

      try {
        const result = await getDiagnosisResult(requestId);
        if (checkCancelled()) return;
        if (result.status === 'completed' || result.status === 'failed') {
          onResult(result);
          return;
        }
        currentDelay = pollInterval;
      } catch (err) {
        if (checkCancelled()) return;
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
