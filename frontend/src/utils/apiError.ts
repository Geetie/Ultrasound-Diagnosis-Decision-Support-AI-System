/**
 * 统一的接口错误取文案工具。
 *
 * 后端错误体为 `ApiErrorResponse`（见 `types/api.ts`），但 axios 抛出的是
 * `unknown`，直接 `catch (err: any)` 会绕过类型检查（且被 lint 禁止）。
 * 这里做一次安全的结构化收窄：只取可用的错误文案，取不到则用兜底文案。
 */

export interface ApiErrorBody {
  error_code?: string;
  error_message?: string;
  detail?: string;
  suggestion?: string;
  retryable?: boolean;
  retry_strategy?: 'no_retry' | 'retry_after_5s' | 'retry_with_fallback';
}

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  return typeof value === 'object' && value !== null;
}

/** 从 axios 异常中取出后端 error_message；取不到时返回 fallback。 */
export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (isApiErrorBody(error)) {
    const response = (error as { response?: { data?: unknown } }).response;
    if (response && isApiErrorBody(response.data)) {
      const body = response.data as ApiErrorBody;
      if (body.error_message) return body.error_message;
      if (body.detail) return body.detail;
    }
    if (typeof (error as { message?: unknown }).message === 'string') {
      const message = (error as { message: string }).message;
      // 网络层报错（如 "Network Error"）对医生没有意义，仍用兜底文案
      if (message && !/network|timeout/i.test(message)) return message;
    }
  }
  return fallback;
}

/**
 * 从 axios 异常中取出后端 error_code（如 E1008 未同意 / E1010 已撤回 /
 * E1002 不存在）—— 文案可以本地化，错误码才是稳定的分流判据。
 * 取不到返回 null（调用方自行兜底）。
 */
export function getApiErrorCode(error: unknown): string | null {
  if (!isApiErrorBody(error)) return null;
  const response = (error as { response?: { data?: unknown } }).response;
  if (response && isApiErrorBody(response.data)) {
    const code = (response.data as ApiErrorBody).error_code;
    if (typeof code === 'string' && code.length > 0) return code;
  }
  return null;
}

/** 判断错误是否可重试（后端 retryable 字段，缺省视为可重试）。 */
export function isRetryableError(error: unknown): boolean {
  if (isApiErrorBody(error)) {
    const response = (error as { response?: { data?: unknown } }).response;
    if (response && isApiErrorBody(response.data)) {
      const body = response.data as ApiErrorBody;
      if (typeof body.retryable === 'boolean') return body.retryable;
    }
  }
  return true;
}
