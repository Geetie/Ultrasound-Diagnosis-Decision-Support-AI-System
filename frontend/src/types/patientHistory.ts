/**
 * 患者历史检查（第二梯队③ 历史对比阅片，A.54）。
 *
 * 与后端 `app/models/schemas.py` 的 `PatientHistoryResponse` 一一对应。
 * 对比必须**同器官**才有临床意义，故后端同时给 `organs`（历史出现过的器官）
 * 与 `comparable_organs`（出现 ≥2 次、真正能前后对比的器官）。
 */

export interface PatientHistorySegmentation {
  available: boolean;
  area_mm2?: number | null;
  area_px?: number | null;
  frame_index?: number | null;
  total_frames?: number | null;
  selection_policy?: string | null;
  organ?: string | null;
  /** available=false 时的原因（如 bypass_disabled）—— 必须显示，不能当成"没有病灶" */
  reason?: string | null;
}

export interface PatientHistoryItem {
  session_id: string;
  created_at: string;
  status: string;
  target_organs: string[];
  organ?: string | null;
  knowledge_base_version?: string | null;
  request_id?: string | null;
  confidence?: number | null;
  /** 结论摘要（后端优先取报告「诊断意见」，无报告时取管线原始结论） */
  conclusion?: string | null;
  report_id?: string | null;
  report_status?: string | null;
  report_signed?: boolean;
  signed_at?: string | null;
  image_count?: number;
  first_image_id?: string | null;
  segmentation?: PatientHistorySegmentation | null;
}

export interface PatientHistoryResponse {
  patient_id: string;
  /** false = patients 表里没有这位患者（与"有患者但没检查"区分，前端据此引导建档） */
  patient_exists: boolean;
  total: number;
  organs: string[];
  comparable_organs: string[];
  items: PatientHistoryItem[];
}
