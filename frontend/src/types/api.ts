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

export interface DiagnosisResult {
  request_id: string;
  status: string;
  consistency_flag: 'consistent' | 'inconsistent' | 'partial_consistent';
  desensitization_status: 'full' | 'degraded';
  consent_status_at_output: 'granted' | 'withdrawn';
  fusion_status: 'normal' | 'bypassed';
  diagnosis: Record<string, unknown>;
  evidence: Record<string, unknown>;
  quality_control: Record<string, unknown>;
  requires_manual_review: boolean;
  review_reason: string | null;
  ai_diagnosis_failed: boolean;
  hallucination_severity: 'severe' | 'moderate' | 'mild' | null;
  version_compatibility: 'compatible' | 'incompatible';
  processing_time_ms: number;
  model_version: string;
  knowledge_base_version: string;
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
