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
  // === 医生直接信息需求（M1~M12），全部可选：未实现时后端返回空值，
  // 前端按"无数据不渲染"处理 ===
  measurement?: Measurement | null;
  differential_diagnoses?: DifferentialDiagnosis[];
  malignancy_risk?: MalignancyRisk | null;
  clinical_actions?: ClinicalAction[];
  patient_explanation?: PatientExplanation | null;
  research_metrics?: ResearchMetrics | null;
  cross_modality_hints?: CrossModalityHint[];
  posterior_acoustic?: PosteriorAcoustic | null;
}

// ===== 医生直接信息需求（M1~M12）契约类型 =====
// 与后端 app/models/schemas.py 中同名模型一一对应。
export type PlaneType = 'transverse' | 'longitudinal' | 'non_standard';
export type PosteriorAcoustic = 'enhancement' | 'attenuation' | 'shadowing' | 'none' | 'mixed';
export type RiskFlag = 'high';
export type SuggestedAction = 'start_standard_evaluation';
export type MalignancyRiskLevel = 'low' | 'medium' | 'high';
export type ClinicalActionUrgency = 'routine' | 'soon' | 'urgent';
export type ImageQualityImpact = 'none' | 'minor' | 'major';

/** M1 切面标准性评估（帧级属性） */
export interface PlaneAssessment {
  plane_type: PlaneType;
  is_standard: boolean;
  plane_score: number;
  organ_code?: string | null;
  issues: string[];
  required_structures_visible: string[];
}

/** M6 图像质量评估（帧级属性），各分项 0.0-1.0 越高越好 */
export interface ImageQuality {
  overall_score: number;
  blur_score?: number | null;
  artifact_score?: number | null;
  gain_score?: number | null;
  issues: string[];
  diagnostic_impact?: ImageQualityImpact | null;
}

/** M3 结构化测量，长度单位 mm、体积 mm^3 */
export interface Measurement {
  long_mm?: number | null;
  short_mm?: number | null;
  anteroposterior_mm?: number | null;
  aspect_ratio?: number | null;
  volume_mm3?: number | null;
  distance_to_capsule_mm?: number | null;
  distance_to_organ_edge_mm?: number | null;
  distance_to_skin_mm?: number | null;
  measurement_type?: string | null;
  unit: string;
  /**
   * 尺寸来源：dicom = 取自 DICOM PixelSpacing；default = 回退默认间距（估算值，
   * 必须提示医生）；text = 来自报告文本表述。
   */
  pixel_spacing_source: 'dicom' | 'default' | 'text';
}

/** M4 鉴别诊断条目，按可能性降序 */
export interface DifferentialDiagnosis {
  rank: number;
  disease_name: string;
  /** 模型未给出可能性时为 null（界面显示"—"，不反推数值） */
  confidence?: number | null;
  disease_code?: string | null;
  supporting_evidence: string[];
  excluding_evidence: string[];
}

/** M5 恶性概率的特征贡献项 */
export interface MalignancyRiskBasis {
  feature: string;
  contribution: number;
}

/** M5 恶性概率（与 AI confidence 语义不同，必须分开展示） */
export interface MalignancyRisk {
  probability: number;
  level: MalignancyRiskLevel;
  basis: MalignancyRiskBasis[];
  source_reference?: string | null;
}

/** M7 转诊/处置建议 */
export interface ClinicalAction {
  action_type: string;
  target_department?: string | null;
  urgency: ClinicalActionUrgency;
  trigger_condition?: string | null;
  description?: string | null;
  /** 基层 | 三甲 | 通用 */
  applicable_level?: string | null;
  source_reference?: string | null;
}

/** M10 患者解释话术（默认不入正式报告） */
export interface PatientExplanation {
  explanation: string;
  reassurance?: string | null;
  next_step?: string | null;
  source_reference?: string | null;
}

export interface BloodFlowParameters {
  psv?: number | null;
  edv?: number | null;
  ri?: number | null;
  unit: string;
}

export interface AIExtractedFeatures {
  texture_homogeneity?: number | null;
  edge_sharpness?: number | null;
  feature_vector_hash?: string | null;
}

export interface ModelConfidenceDistribution {
  benign?: number | null;
  malignant?: number | null;
  indeterminate?: number | null;
}

/** M11 科研量化指标（默认折叠展示） */
export interface ResearchMetrics {
  elasticity_score?: number | null;
  elasticity_unit?: string | null;
  blood_flow?: BloodFlowParameters | null;
  ai_features?: AIExtractedFeatures | null;
  confidence_distribution?: ModelConfidenceDistribution | null;
}

/** M12 跨模态对照提示 */
export interface CrossModalityHint {
  modality: string;
  finding: string;
  clinical_value?: string | null;
  source_reference?: string | null;
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
