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
    /**
     * 结构化患者条件，供安全网关**禁忌判定**使用（后端 `PatientContext.conditions`）。
     *
     * ⚠️ 必须发**规范码**（`pregnancy` / `contrast_allergy` / `anticoagulant_therapy`），
     * 不是界面上显示的中文标签 —— SG 的 `_CONDITION_ALIASES` 只认码，
     * 中文串永远不命中（= 勾了禁忌也不拦，反向 fail-open）。
     * 码表唯一来源：`@/components/workspace/clinicalInfo` 的 `CONDITION_CODES`。
     *
     * ⚠️ 语义三态（`app/models/schemas.py:193-197`）：
     *   · `undefined` → 不转发该键 → SG 保守判「缺数据即命中」→ 每次都要人工复核；
     *   · `[]`        → 已确认无条件 → SG 正常评估 → 不命中；
     *   · `['pregnancy']` → 按别名匹配。
     * 前端的"一项都没勾"是医生的确认，**要送 `[]`**，不要留 undefined。
     */
    conditions?: string[];
  };
  retry?: boolean;
  idempotency_key?: string;
}

/**
 * ===== 多器官联合诊断（T-GB-AGT-01，此前前端零调用）=====
 *
 * 后端：`POST /api/v1/diagnosis/multi-organ`（`app/api/v1/diagnosis.py:546`），
 * 一次调用按会话 `target_organs` 串行跑完全部器官。会话部位数 ≥ 2 时才该走它。
 */

import type { OrganStatus } from './session';

/** 单器官执行状态：**复用** `types/session.ts` 的 `OrganStatus`（同一后端枚举，不另立一份）。 */
export type { OrganStatus };

/** 单器官执行结果（后端 `OrganDiagnosisStatus`）。`reason` 是**可读失败原因**。 */
export interface OrganDiagnosisStatus {
  organ: string;
  status: OrganStatus;
  /** 该器官的 request_id —— 「单独重跑该器官」与报告入口靠它。 */
  request_id?: string | null;
  confidence: number;
  elapsed_ms: number;
  /** 失败/跳过的原因（后端原样给，例如总预算耗尽）。null = 正常完成。 */
  reason?: string | null;
}

/**
 * 跨器官融合块（A.88）。**`status` 三态必须区分显示**：
 *   · `ok`                      → 确实做了冲突检测 + 一致性核对，可以报结果；
 *   · `insufficient_responses`  → 有效结论不足 2 份，**前提不成立**（不是"无冲突"）；
 *   · `unavailable`             → 融合引擎不可用，**没跑**（不是"没问题"）。
 * 把后两种显示成"多部位一致"就是把"没跑"说成"没问题"。
 */
export interface CrossOrganBlock {
  status: 'ok' | 'insufficient_responses' | 'unavailable' | string;
  session_id?: string;
  organ_count?: number;
  organs?: string[];
  /** 三态非 ok 时的机器可读原因（`need >= 2 organ conclusions, got N` / `fusion_engine_unavailable`）。 */
  reason?: string;
  conflict?: {
    has_conflict: boolean;
    conflict_types: string[];
    resolution_suggestion: string;
    detail?: string | null;
  };
  consistency?: {
    is_consistent: boolean;
    severity: string;
    compared_with_prior: boolean;
    compared_with_guidelines: boolean;
    inconsistent_organ_pairs?: string[][];
    detail?: string | null;
  };
  needs_review?: boolean;
}

/** 多器官联合诊断响应（后端 `MultiOrganDiagnosisResponse`）。 */
export interface MultiOrganDiagnosisResponse {
  session_id: string;
  session_status: string;
  organs: string[];
  organ_statuses: Record<string, OrganStatus>;
  organ_results: OrganDiagnosisStatus[];
  completed_organs: string[];
  failed_organs: string[];
  skipped_organs: string[];
  needs_review_organs: string[];
  /** 部分完成：有器官没跑成。**不得**当作"全部完成"渲染。 */
  partial: boolean;
  total_ms: number;
  /** 空对象 = 未执行（后端 `default_factory=dict`）。 */
  cross_organ?: CrossOrganBlock | Record<string, never>;
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
  /** 生成进度（A.43，processing 中间态才带）：已生成字符数 */
  generation_progress_chars?: number;
  /**
   * 安全预览（A.46，processing 中间态才带）：**已通过段落级 SG** 的生成文本尾部
   * —— SG 拒绝时前端停止显示（Never 显示未过 SG 的原始输出 —— 临床红线）。
   */
  generation_preview?: string;
  hallucination_severity: 'severe' | 'moderate' | 'mild' | null;
  /**
   * 以下四项在真机实测响应里**不出现**（2026-10-02 req_20261002_da4fa542），
   * 故全部可选。设计文档 16-核定报告 §N-01 声称它们是硬编码常量。
   */
  version_compatibility?: 'compatible' | 'incompatible';
  processing_time_ms?: number;
  model_version?: string;
  knowledge_base_version?: string;
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
  /**
   * 分割旁路结果（2026-10-03 后端挂入，形态见 app/models/schemas.py 注释）：
   * available=true 带 bbox/面积/概率；available=false 带 reason —— 前端必须显示
   * 「未做分割：<原因>」，**不能**显示成「未检出结节」（后端契约原文）。
   * 前端直到 2026-10-08 才第一次消费它（此前病灶标注全是设计稿写死的示意框）。
   */
  nodule_segmentation?: Record<string, unknown> | null;
  /**
   * T2a 影像分类旁路（02-1 后端挂入）：**辅助参考**，与 nodule_segmentation
   * 同风格的可选 Dict。available=true 带 probs/predicted/malignant_prob；
   * available=false 带 reason（封闭集合）——前端必须显示「未做影像分类：<原因>」，
   * **不能**显示成"未检出恶性"。红线：不得与结论并列同层级，展示必须带
   * 「辅助参考」标注，不参与 status/confidence。
   */
  t2a_classification?: Record<string, unknown> | null;
  /**
   * 相似病例（A.82 后端挂入）：**参考信息**，与「知识依据」分开。
   * 只含 image_id / score / organ / date —— **不含** patient_id / session_id。
   * 后端固定带 `label` 与 `disclaimer`，前端必须原样展示（不得省略"参考"字样）。
   */
  similar_cases?: SimilarCasesBlock | null;
}

/** 相似病例块（A.82）。后端 `response_block()` 的输出形状。 */
export interface SimilarCasesBlock {
  /** 固定为「本人历史影像（参考）」——前端**不得**改写 */
  label: string;
  /** 免责声明：不得省略，不得折叠默认隐藏 */
  disclaimer: string;
  status: string;
  count: number;
  scope?: string;
  reason?: string;
  cases?: SimilarCase[];
}

export interface SimilarCase {
  image_id: string;
  score: number;
  date?: string;
  organ?: string;
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

// ===== 医生直接信息需求（M1~M12）契约类型 =====// 与后端 app/models/schemas.py 中同名模型一一对应。
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
  /** 详情端点补充的脱敏人口学（A.36 P0：身份数据驱动）。缺数据 = undefined。 */
  patient_name_masked?: string | null;
  gender?: string | null;
  age?: number | null;
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
