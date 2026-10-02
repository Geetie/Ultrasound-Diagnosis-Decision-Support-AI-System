/**
 * 报告（M8 / T-GB-API-02）类型定义。
 *
 * 与后端 `app/models/schemas.py` 的 `ReportDraftRequest` / `ReportResponse` /
 * `ReportSignRequest` / `ReportPushRequest` 一一对应；
 * `completeness` 的结构来自 `app/services/report_service.check_completeness`。
 */

/** 报告完整性校验结果（后端 `check_completeness` 的输出，直接供面板使用）。 */
export interface ReportCompleteness {
  /** 归一化后的器官标识（用于选模板） */
  organ: string;
  /** 实际命中的模板名 */
  template: string;
  /** 该器官模板要求的必需字段 */
  required: string[];
  /** 其中缺失的字段 */
  missing: string[];
  /** 是否全部齐备 */
  passed: boolean;
}

export interface ReportResponse {
  report_id: string;
  request_id: string;
  session_id: string | null;
  patient_id: string | null;
  organ: string | null;
  /** draft / signed / pushed（后端字符串枚举，前端不做封闭假设） */
  status: string;
  template_version: string | null;
  /** 正文摘要，用于校验正文是否被改动 */
  content_digest: string | null;
  signature_present: boolean;
  /** 仅在显式请求校验（verify=true）时才有意义 */
  signature_verified: boolean;
  signing_key_name: string | null;
  signed_at: string | null;
  signed_by: string | null;
  pacs_adapter: string | null;
  pacs_status: string | null;
  pacs_message: string | null;
  pacs_attempts: number | null;
  pushed_at: string | null;
  created_at: string | null;
  content: Record<string, unknown> | null;
  /** 按器官模板渲染后的正文 */
  rendered: string | null;
  completeness: ReportCompleteness | null;
}

export interface ReportDraftRequest {
  request_id: string;
  /** 生成后立即签名 */
  sign?: boolean;
  /** 生成后立即推送（要求已签名） */
  push?: boolean;
  operator_id?: string;
}

export interface ReportSignRequest {
  operator_id?: string;
}

export interface ReportPushRequest {
  /** 推送适配器名（pacs / his / …），缺省用后端配置值 */
  adapter?: string;
}

export interface ReportVerifyResponse {
  report_id: string;
  signature_present: boolean;
  signature_verified: boolean;
  content_digest: string | null;
  signing_key_name: string | null;
}
