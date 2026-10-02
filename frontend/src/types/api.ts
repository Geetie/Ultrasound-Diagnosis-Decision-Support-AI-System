export interface ApiErrorResponse {
  error_code: string;
  error_message: string;
  detail?: string;
  request_id?: string;
  suggestion?: string;
  retryable: boolean;
  retry_strategy: 'no_retry' | 'retry_after_5s' | 'retry_with_fallback';
}

export interface DiagnosisRequest {
  session_id: string;
  image_ids: string[];
  clinical_text?: string;
  voice_text?: string | null;
  target_organ: 'thyroid' | 'liver' | 'cardiac';
  response_tier: 'realtime' | 'standard' | 'full';
  patient_context?: {
    age?: number;
    gender?: 'M' | 'F';
    relevant_history?: string;
  };
  retry?: boolean;
  idempotency_key?: string;
}

export interface DiagnosisRequestResponse {
  request_id: string;
  session_id: string;
  status: 'processing' | 'completed' | 'timeout' | 'failed';
  estimated_time_ms: number;
  response_tier: string;
}

/**
 * `diagnosis.timings_ms` 的**实测**键名（2026-10-02 真机响应）。
 * 注意是 `dispatcher.invoke_ms`，不是 `dispatch`。
 */
export interface DiagnosisTimings {
  db_read_ms?: number;
  'fusion.fuse_ms'?: number;
  'pipeline.total_ms'?: number;
  'sg.check_output_ms'?: number;
  'dispatcher.invoke_ms'?: number;
}

/** 安全网关裁决。 */
export interface SafetyGatewayResult {
  passed: boolean;
  allowed: boolean;
  message: string;
  error_code: string;
  degraded_mode: boolean;
  /**
   * 实测值 `unknown_blocked`，**不在**顶层 `consent_status_at_output` 的枚举里。
   * 且与顶层可能不一致：同一条响应里顶层是 `granted`、这里是 `unknown_blocked`。
   */
  consent_status_at_output?: string;
}

/** 融合层产物。`self_rag_passed` 在这里，不在结果顶层。 */
export interface FusionResult {
  content: string;
  needs_review: boolean;
  review_reason: string | null;
  crag_recovered: boolean;
  crag_triggered: boolean;
  /** 实测可以是 `true` —— 设计文档「16-核定报告 §N-01」称其恒为 `False`，与实测不符。 */
  self_rag_passed: boolean;
  final_confidence: number;
  total_latency_ms: number;
}

/**
 * `diagnosis` 内层全部**可选**。
 *
 * 依据实测（2026-10-02 req_20261002_da4fa542）：真实响应里出现过的键是
 * `organ` / `rollout` / `routing` / `sg_result` / `timings_ms` /
 * `completed_at` / `fusion_result` / `review_reason`。
 * 档位与路由不同则整块可能缺失，界面必须能容错（契约 §0 P1：不满足条件不渲染）。
 */
export interface DiagnosisDetail {
  organ?: string;
  rollout?: Record<string, unknown>;
  routing?: Record<string, unknown>;
  sg_result?: SafetyGatewayResult;
  timings_ms?: DiagnosisTimings;
  completed_at?: string;
  /** 真实结论文本在这里（`content`），不在 `diagnosis` 顶层。 */
  fusion_result?: FusionResult;
  review_reason?: string | null;
  /** 历史/其它档位可能出现的别名，实测响应未出现，保留兼容。 */
  standard_conclusion?: string;
  conclusion?: string;
  full_conclusion?: string;
  deep_analysis?: string;
}

export interface DiagnosisResult {
  request_id: string;
  status: string;
  /** ⚠️ 实测是**小写** `consistent`；设计文档写的大写 `CONSISTENT` 是错的。 */
  consistency_flag: 'consistent' | 'inconsistent' | 'partial_consistent';
  desensitization_status: 'full' | 'degraded';
  /** 实测顶层取值 `granted`；`unknown_blocked` 出现在 `sg_result` 内。 */
  consent_status_at_output: 'granted' | 'withdrawn' | 'denied' | 'pending';
  /** ⚠️ 实测是**小写** `normal`；设计文档写的大写 `NORMAL` 是错的。 */
  fusion_status: 'normal' | 'bypassed';
  diagnosis: DiagnosisDetail;
  evidence: Record<string, unknown>;
  quality_control?: Record<string, unknown>;
  /**
   * ⚠️ **与 `diagnosis.sg_result.allowed` 存在语义矛盾**，实测同一条响应里：
   *   `status=completed` · `requires_manual_review=false`
   *   但 `sg_result.allowed=false` · `sg_result.message="安全校验服务不可用，请转为人工诊断"`
   * 两者不一致。渲染「确认动作区」（契约 §1：仅有待确认项时出现）时**不能只看本字段**，
   * 否则 SG 明确要求转人工时动作区不出现。
   */
  requires_manual_review: boolean;
  review_reason?: string | null;
  ai_diagnosis_failed: boolean;
  hallucination_severity: 'severe' | 'moderate' | 'mild' | null;
  version_compatibility?: 'compatible' | 'incompatible';
  processing_time_ms?: number;
  model_version?: string;
  knowledge_base_version?: string;
}

/** 工作列表单行（后端 SessionListItem）。字段名与真机响应一致。 */
export interface SessionListItem {
  session_id: string;
  patient_id: string;
  /** 服务端已脱敏（姓+**）。前端**不得**再去猜姓名，也不得显示完整姓名。 */
  patient_name_masked: string | null;
  gender: string | null;
  /** 由服务端从 birth_date 派生；库中 birth_date 为空时为 null（不猜）。 */
  age: number | null;
  status: 'pending' | 'active' | 'partial' | 'completed' | 'needs_review' | 'failed' | 'terminated';
  target_organs: string[];
  created_at: string;
  updated_at: string;
  ttl_remaining_seconds: number | null;
}

/**
 * 工作列表整体。**分组在服务端做完**，前端不再二次分类 ——
 * 契约 P2「一次决策」：只分进行中 / 今日待处理两组。
 */
export interface SessionListResponse {
  in_progress: SessionListItem[];
  pending_today: SessionListItem[];
  total_pending: number;
}

export interface ConsentStatus {
  patient_id: string;
  consent_status: 'granted' | 'denied' | 'withdrawn' | 'pending';
  consented_at: string | null;
  withdrawn_at: string | null;
  consent_version: string;
}

export interface SessionInfo {
  session_id: string;
  patient_id: string;
  device_id: string;
  status: 'pending' | 'active' | 'partial' | 'completed' | 'needs_review' | 'failed' | 'terminated';
  target_organs: string[];
  organ_statuses: Record<string, string>;
  knowledge_base_version: string;
  created_at: string;
  updated_at: string;
  expired_at: string | null;
  consent_status_at_creation: string;
}

export interface ImagingUploadResponse {
  image_id: string;
  session_id: string;
  metadata: {
    patient_id: string;
    modality: string;
    body_part: string;
    acquisition_date: string;
  };
  status: 'accepted';
}

export interface FeedbackRequest {
  request_id: string;
  session_id: string;
  feedback_type: 'correction' | 'supplement' | 'new_knowledge' | 'rule_change';
  feedback_level: 'L2' | 'L3';
  content: {
    field: string;
    original_value: string;
    corrected_value: string;
    reason: string;
  };
  reviewer_id?: string;
}
