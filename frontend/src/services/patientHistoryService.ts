import apiClient from './api';

import type { PatientHistoryResponse } from '@/types/patientHistory';

/**
 * 患者历史检查（第二梯队③ 历史对比阅片，A.54）。
 *
 * 后端：`GET /api/v1/patients/{patient_id}/sessions`。
 * 为什么不用 `GET /api/v1/sessions`：那是**工作列表**语义（只含进行中 + 今日待处理，
 * 不含 completed），而历史对比看的恰恰都是已完成检查 —— 用工作列表会导致
 * "刚做完的检查立刻从历史里消失"。
 */
export async function fetchPatientHistory(
  patientId: string,
  options: { limit?: number; organ?: string } = {},
): Promise<PatientHistoryResponse> {
  const params: Record<string, string | number> = { limit: options.limit ?? 20 };
  if (options.organ) params.organ = options.organ;
  const response = await apiClient.get<PatientHistoryResponse>(
    `/patients/${encodeURIComponent(patientId)}/sessions`,
    { params },
  );
  return response.data;
}
