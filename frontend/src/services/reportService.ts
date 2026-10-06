import apiClient from './api';
import type {
  ReportDraftRequest,
  ReportPushRequest,
  ReportResponse,
  ReportSignRequest,
  ReportVerifyResponse,
} from '@/types/report';

/** 生成报告草稿（可一步签名/推送，见 `ReportDraftRequest`）。 */
export async function createReportDraft(data: ReportDraftRequest): Promise<ReportResponse> {
  const response = await apiClient.post<ReportResponse>('/report', data);
  return response.data;
}

/** 取某次诊断请求的最新报告。 */
export async function getReportByRequest(requestId: string): Promise<ReportResponse> {
  const response = await apiClient.get<ReportResponse>(`/report/by-request/${requestId}`);
  return response.data;
}

/**
 * 医生补录段落（A.32）：合并进草稿、重渲染、重算完整性。
 * 只允许草稿；已签名报告会被服务端拒绝（正文由 HSM 锁定）。
 */
export async function updateReportSections(
  reportId: string,
  sections: Record<string, string>
): Promise<ReportResponse> {
  const response = await apiClient.patch<ReportResponse>(`/report/${reportId}`, { sections });
  return response.data;
}

/** 按报告 ID 读取；`verify=true` 时附带数字签名校验结果。 */
export async function getReport(reportId: string, verify = false): Promise<ReportResponse> {
  const response = await apiClient.get<ReportResponse>(
    `/report/${reportId}`,
    verify ? { params: { verify: true } } : undefined
  );
  return response.data;
}

/** 对报告正文做 HSM 数字签名（后端拒绝重复签名）。 */
export async function signReport(
  reportId: string,
  data: ReportSignRequest = {}
): Promise<ReportResponse> {
  const response = await apiClient.post<ReportResponse>(`/report/${reportId}/sign`, data);
  return response.data;
}

/** 推送到 PACS/HIS（要求已签名；推送失败不改签名状态，可重推）。 */
export async function pushReport(
  reportId: string,
  data: ReportPushRequest = {}
): Promise<ReportResponse> {
  const response = await apiClient.post<ReportResponse>(`/report/${reportId}/push`, data);
  return response.data;
}

/** 校验签名与正文是否匹配（正文被改动即失败）。 */
export async function verifyReportSignature(reportId: string): Promise<ReportVerifyResponse> {
  const response = await apiClient.post<ReportVerifyResponse>(`/report/${reportId}/verify`);
  return response.data;
}
