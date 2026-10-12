from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ============== 枚举定义 ==============
class SessionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PARTIAL = "partial"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    TERMINATED = "terminated"


class OrganStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_REVIEW = "needs_review"


# CONSISTENCY: Must match safety_gateway.validation_result.ConsentStatus exactly. Any change must be reflected in both modules.
class ConsentStatus(str, Enum):
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"
    DENIED = "denied"
    PENDING = "pending"  # 同意书已提交但尚未审批，规范扩展状态


class ResponseTier(str, Enum):
    REALTIME = "realtime"
    STANDARD = "standard"
    FULL = "full"


class FeedbackType(str, Enum):
    CORRECTION = "correction"
    SUPPLEMENT = "supplement"
    NEW_KNOWLEDGE = "new_knowledge"
    RULE_CHANGE = "rule_change"


class FeedbackLevel(str, Enum):
    L2 = "L2"
    L3 = "L3"


class Organ(str, Enum):
    THYROID = "thyroid"
    LIVER = "liver"
    CARDIAC = "cardiac"


class FusionStatus(str, Enum):
    NORMAL = "normal"
    BYPASSED = "bypassed"


class DesensitizationStatus(str, Enum):
    FULL = "full"
    DEGRADED = "degraded"


class ConsistencyFlag(str, Enum):
    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    PARTIAL = "partial_consistent"


class RetryStrategy(str, Enum):
    FULL_RETRIEVAL = "full_retrieval"
    GENERATION_ONLY = "generation_only"
    QUALITY_CHECK_ONLY = "quality_check_only"


class HallucinationSeverity(str, Enum):
    SEVERE = "severe"
    MODERATE = "moderate"
    MILD = "mild"


class RetryStrategyType(str, Enum):
    RETRY_AFTER_5S = "retry_after_5s"
    RETRY_AFTER_30S = "retry_after_30s"
    RETRY_WITH_FALLBACK = "retry_with_fallback"
    NO_RETRY = "no_retry"


# ============== 医生直接信息需求（M1~M12）枚举 ==============
# 来源：13-医生直接信息需求缺口实施指南.md（M1~M12）。
class PlaneType(str, Enum):
    """切面类型（M1 切面标准性评估）。"""

    TRANSVERSE = "transverse"
    LONGITUDINAL = "longitudinal"
    NON_STANDARD = "non_standard"


class PosteriorAcoustic(str, Enum):
    """后方回声特征（M9，UDAF 属性扩展）。"""

    ENHANCEMENT = "enhancement"
    ATTENUATION = "attenuation"
    SHADOWING = "shadowing"
    NONE = "none"
    MIXED = "mixed"


class RiskFlag(str, Enum):
    """B+ 级高危导航标记（M2）。

    只表达"需关注"，**不含**分级词汇或病种编码，因此不违反
    ``safety_gateway`` 的 FORBIDDEN_GRADING_TERMS 约束。
    """

    HIGH = "high"


class SuggestedAction(str, Enum):
    """B+ 级建议动作（M2）。"""

    START_STANDARD_EVALUATION = "start_standard_evaluation"


class MalignancyRiskLevel(str, Enum):
    """恶性概率分级（M5）。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ClinicalActionUrgency(str, Enum):
    """处置紧急度（M7）。"""

    ROUTINE = "routine"
    SOON = "soon"
    URGENT = "urgent"


class ImageQualityImpact(str, Enum):
    """图像质量对诊断的影响程度（M6）。"""

    NONE = "none"
    MINOR = "minor"
    MAJOR = "major"


# ============== 基础模型 ==============
class PatientContext(BaseModel):
    age: int | None = Field(None, ge=0, le=150)
    gender: str | None = None
    relevant_history: str | None = None
    # 结构化患者条件，供安全网关的**禁忌判定**使用（例如 ["pregnancy"]、
    # ["contrast_allergy"]、["anticoagulant_therapy"]）。
    #
    # 2026-09-29 真机暴露：此前 PatientContext 只有 age/gender/relevant_history 三个
    # 自由文本字段，而 SG 的 `_match_condition` 认的是**结构化的
    # `patient_conditions` 列表**（别名见 safety_gateway/contraindication.py）
    # → 应用侧结构上就送不出条件 → 每次诊断都命中"缺条件数据"的保守分支
    # （缺数据即判命中）→ 恒 requires_manual_review，系统永远给不出自动结论。
    #
    # 语义约定（重要，决定判定的方向）：
    #   * `None`（未提供）  → 不转发该键，SG 维持**保守**行为（缺数据即视为命中）；
    #   * `[]`（确认无条件）→ 如实转发空列表，SG 正常评估 → 不命中；
    #   * `["pregnancy"]`  → 如实转发，按别名匹配。
    conditions: list[str] | None = None


class BaseResponse(BaseModel):
    success: bool = True
    message: str | None = None


# ============== 错误响应 ==============
class ErrorResponse(BaseModel):
    error_code: str = Field(..., description="错误码，如 E1001、E2001 等")
    error_message: str = Field(..., description="错误描述")
    detail: str | None = None
    request_id: str | None = None
    suggestion: str | None = None
    retryable: bool = False
    retry_strategy: RetryStrategyType = RetryStrategyType.NO_RETRY


# ============== 诊断相关模型 ==============
class DiagnosisRequest(BaseModel):
    session_id: str
    image_ids: list[str]
    clinical_text: str | None = None
    voice_text: str | None = None
    target_organ: Organ
    response_tier: ResponseTier = ResponseTier.FULL
    patient_context: PatientContext | None = None
    retry: bool = False
    retry_strategy: RetryStrategy | None = None
    idempotency_key: str | None = None


class DiagnosisRequestResponse(BaseModel):
    request_id: str
    session_id: str
    status: str = "processing"
    estimated_time_ms: int = 3000
    response_tier: ResponseTier


class DiagnosisEvidence(BaseModel):
    knowledge_ids: list[str] | None = None
    content: dict[str, Any] | None = None


class DiagnosisQualityControl(BaseModel):
    confidence_score: float | None = None
    self_rag_passed: bool = False
    consistency_check_passed: bool = False


# ============== 医生直接信息需求（M1~M12）共享模型 ==============
# 这批模型同时被两处消费：
# 1. ``app/agents/protocols.AgentResponse``（专科智能体结构化输出）；
# 2. ``app/models/schemas.DiagnosisResult``（对外结果契约，经管线唯一组装点填充）。
# 全部字段可选/带默认值，保证既有构造点（均为关键字参数）不受影响。
class PlaneAssessment(BaseModel):
    """切面标准性评估（M1）。帧级属性，由边缘端切面评估头产出。"""

    plane_type: PlaneType
    is_standard: bool
    plane_score: float = Field(ge=0.0, le=1.0, description="切面标准化程度，0.0-1.0")
    organ_code: str | None = None
    issues: list[str] = Field(default_factory=list)
    required_structures_visible: list[str] = Field(default_factory=list)


class ImageQuality(BaseModel):
    """图像质量评估（M6）。各分项 0.0-1.0，越高越好。"""

    overall_score: float = Field(ge=0.0, le=1.0)
    blur_score: float | None = Field(default=None, ge=0.0, le=1.0)
    artifact_score: float | None = Field(default=None, ge=0.0, le=1.0)
    gain_score: float | None = Field(default=None, ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)
    diagnostic_impact: ImageQualityImpact | None = None


class Measurement(BaseModel):
    """结构化测量（M3）。长度单位统一 mm，体积单位 mm^3。

    ``pixel_spacing_source`` 标明尺寸换算依据：``dicom`` 表示取自 DICOM
    ``PixelSpacing``/``ImagerPixelSpacing``，``default`` 表示 DICOM 缺失时
    回退到 ``DEFAULT_PIXEL_SPACING_MM``（此时测量值为估算，前端需标注）。
    """

    long_mm: float | None = None
    short_mm: float | None = None
    anteroposterior_mm: float | None = None
    aspect_ratio: float | None = None
    volume_mm3: float | None = None
    distance_to_capsule_mm: float | None = None
    distance_to_organ_edge_mm: float | None = None
    distance_to_skin_mm: float | None = None
    measurement_type: str | None = None
    unit: str = "mm"
    pixel_spacing_source: str = "default"


class DifferentialDiagnosis(BaseModel):
    """鉴别诊断条目（M4），按可能性降序排列（rank 从 1 开始）。

    ``confidence`` 可选：模型未给出可能性数值时保持 ``None``（界面显示"—"）。
    按下标反推一个数值会凭空制造看似精确的概率，比不给更危险。
    """

    rank: int = Field(ge=1)
    disease_name: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    disease_code: str | None = None
    supporting_evidence: list[str] = Field(default_factory=list)
    excluding_evidence: list[str] = Field(default_factory=list)


class MalignancyRiskBasis(BaseModel):
    """恶性概率的单个特征贡献项（M5）。"""

    feature: str
    contribution: float


class MalignancyRisk(BaseModel):
    """恶性概率（M5）。

    与 ``AgentResponse.confidence``/``DiagnosisQualityControl.confidence_score``
    语义不同：前者是"AI 对这个结论有多确信"，本字段是"该病灶为恶性的
    概率估计"。两者必须分开展示，避免医生混淆。
    """

    probability: float = Field(ge=0.0, le=1.0)
    level: MalignancyRiskLevel
    basis: list[MalignancyRiskBasis] = Field(default_factory=list)
    source_reference: str | None = None


class ClinicalAction(BaseModel):
    """转诊/处置建议（M7）。"""

    action_type: str
    target_department: str | None = None
    urgency: ClinicalActionUrgency = ClinicalActionUrgency.ROUTINE
    trigger_condition: str | None = None
    description: str | None = None
    # 适用机构层级：基层 | 三甲 | 通用（前端按机构差异化展示）
    applicable_level: str | None = None
    source_reference: str | None = None


class PatientExplanation(BaseModel):
    """患者解释话术（M10）。默认不进入正式报告，由医生选择展示/打印。"""

    explanation: str
    reassurance: str | None = None
    next_step: str | None = None
    source_reference: str | None = None


class BloodFlowParameters(BaseModel):
    """血流参数（M11 科研量化指标）。"""

    psv: float | None = None
    edv: float | None = None
    ri: float | None = None
    unit: str = "cm/s"


class AIExtractedFeatures(BaseModel):
    """AI 抽取的量化特征（M11）。"""

    texture_homogeneity: float | None = None
    edge_sharpness: float | None = None
    feature_vector_hash: str | None = None


class ModelConfidenceDistribution(BaseModel):
    """模型置信度分布（M11）。"""

    benign: float | None = None
    malignant: float | None = None
    indeterminate: float | None = None


class ResearchMetrics(BaseModel):
    """科研量化指标（M11），前端默认折叠展示。"""

    elasticity_score: float | None = None
    elasticity_unit: str | None = None
    blood_flow: BloodFlowParameters | None = None
    ai_features: AIExtractedFeatures | None = None
    confidence_distribution: ModelConfidenceDistribution | None = None


class CrossModalityHint(BaseModel):
    """跨模态对照提示（M12）。"""

    modality: str  # CT | MRI | pathology
    finding: str
    clinical_value: str | None = None
    source_reference: str | None = None


class DiagnosisResult(BaseModel):
    request_id: str
    status: str
    consistency_flag: ConsistencyFlag | None = None
    desensitization_status: DesensitizationStatus
    consent_status_at_output: ConsentStatus
    fusion_status: FusionStatus
    diagnosis: dict[str, Any]
    evidence: DiagnosisEvidence | None = None
    quality_control: DiagnosisQualityControl | None = None
    requires_manual_review: bool = False
    review_reason: str | None = None
    ai_diagnosis_failed: bool = False
    # 生成进度（A.43，processing 中间态才带）：已生成字符数 —— 前端「分析中」
    # 显示「已生成 N 字」，秒数走动 + 字数增长 = 生成确实在推进
    generation_progress_chars: int | None = None
    # 安全预览（A.46，processing 中间态才带）：**已通过段落级 SG** 的生成文本尾部
    # —— AWS 同步护栏模式：段落过 check_output 才进预览键，全文 SG 终态兜底；
    # refused 时前端停止显示预览（Never 显示未过 SG 的原始输出 —— 临床红线）
    generation_preview: str | None = None
    hallucination_severity: HallucinationSeverity | None = None
    version_compatibility: str = "compatible"
    processing_time_ms: int
    model_version: str | None = None
    knowledge_base_version: str | None = None
    # A.52：实际产出结论的生成模型标识（如 Qwen2.5-7B-Instruct@rev），与
    # `model_version`（诊断引擎版本，参与兼容矩阵）分行溯源：前者答"哪个模型"、
    # 后者答"哪一版能力"。
    llm_model: str | None = None
    # === 医生直接信息需求（M1~M12）===
    # 全部为可选展示型字段：新字段先承载于此契约（并由
    # ``diagnosis_results.findings`` JSONB 落库），避免为展示型数据
    # 引入不必要的表结构迁移。
    measurement: Measurement | None = None
    differential_diagnoses: list[DifferentialDiagnosis] = Field(default_factory=list)
    malignancy_risk: MalignancyRisk | None = None
    clinical_actions: list[ClinicalAction] = Field(default_factory=list)
    patient_explanation: PatientExplanation | None = None
    research_metrics: ResearchMetrics | None = None
    cross_modality_hints: list[CrossModalityHint] = Field(default_factory=list)
    posterior_acoustic: PosteriorAcoustic | None = None
    # 分割旁路结果（2026-10-03）：轮廓/面积/概率/降级原因。
    # 用 Optional[Dict] 而非新 schema —— 它是**旁路辅助信息**，形态还会随
    # 器官与模型演进（甲状腺结节 vs 左心室），过早固化 schema 只会带来
    # 无谓的契约迁移。``available=False`` 时带 ``reason``，前端应显示
    # "未做分割：<原因>"，**不能**显示成"未检出结节"。
    nodule_segmentation: dict[str, Any] | None = None


# ============== DICOM 相关模型 ==============
class ReportUpdateRequest(BaseModel):
    """医生补录段落（A.32）。键白名单在服务层（SUPPLEMENTABLE_SECTIONS）。"""

    sections: dict[str, str]


class DICOMUploadResponse(BaseModel):
    image_id: str
    session_id: str
    metadata: dict[str, Any]
    status: str = "accepted"


class DICOMImageMeta(BaseModel):
    """影像索引条目（2026-10-06 影像回取）。

    刻意**不含** ``storage_path`` / ``patient_id``：前者是服务端内部路径
    （local 绝对路径或 minio:// URL），泄露它等于把存储布局交给客户端；
    后者与 audit_logs 的脱敏口径不一致（dicom_images.patient_id 存的是
    脱敏前的原始 ID，见 imaging.upload_dicom 的 raw_patient_id）。
    """

    image_id: str
    session_id: str
    modality: str | None = None
    width: int | None = None
    height: int | None = None
    frame_count: int | None = None
    size_bytes: int | None = None
    original_filename: str | None = None
    created_at: datetime | None = None


class DICOMImageListResponse(BaseModel):
    session_id: str
    count: int
    images: list[DICOMImageMeta]


class DICOMStreamRequest(BaseModel):
    session_id: str
    frame_data: str
    frame_index: int
    is_key_frame: bool = True
    priority: int = 1


class DICOMStreamResponse(BaseModel):
    session_id: str
    frame_id: str
    navigation_hint: dict[str, Any] | None = None
    status: str = "accepted"


# ============== WebSocket 消息模型 ==============
class WebSocketMessageType(str, Enum):
    FRAME = "frame"
    NAVIGATION = "navigation"
    PING = "ping"
    PONG = "pong"
    AUTH = "auth"
    AUTH_OK = "auth_ok"
    DIAGNOSIS_RESULT = "diagnosis_result"
    SESSION_TERMINATED = "session_terminated"
    NAVIGATION_REVOCATION = "navigation_revocation"
    RECONNECT_ACK = "reconnect_ack"
    ERROR = "error"


class BaseWebSocketMessage(BaseModel):
    type: WebSocketMessageType
    timestamp: int | None = None


class FrameMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.FRAME
    frame_id: str
    frame_index: int


class NavigationAnnotation(BaseModel):
    label: str
    confidence: float
    threshold: float | None = None
    # 兼容两种坐标口径：后端推理路径沿用接口规范示例的像素整数，
    # 边缘端路径使用 0.0-1.0 归一化坐标（edge/safety 校验与前端
    # NavigationOverlay 的口径）。前端类型为 [number, number, number, number]。
    bbox: list[float] | None = None
    # === M2 快速路径高危标记（B+ 级，标注级属性）===
    # 仅标记"需关注"并建议启动标准评估，不含分级词汇/病种编码。
    risk_flag: RiskFlag | None = None
    suggested_action: SuggestedAction | None = None


class NavigationMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.NAVIGATION
    frame_id: str
    annotations: list[NavigationAnnotation] = []
    desensitization_status: DesensitizationStatus = DesensitizationStatus.FULL
    # === M1/M6 帧级属性 ===
    # 切面标准性与图像质量是"这一帧采集"的属性，不属于单个病灶标注，
    # 因此挂在帧级消息上，避免在每个标注里重复。
    plane_assessment: PlaneAssessment | None = None
    image_quality: ImageQuality | None = None


class EdgeNavLabel(BaseModel):
    """边缘端推理输出的单个标注（归一化坐标，见 edge/protocols.NavLabel）。"""

    label: str
    x: float
    y: float
    width: float
    height: float
    confidence: float
    # === M2（B+ 级高危标记），可选以兼容未升级的边缘端 ===
    risk_flag: str | None = None
    suggested_action: str | None = None


class EdgeNavResultMessage(BaseModel):
    """边缘端上报的导航结果（T-GB-INT-02：edge → backend 的上行消息）。

    与 ``edge/websocket_server.EdgeWebSocketClient.push_nav_result`` 的载荷
    一一对应；此前后端**没有任何消费者**，边缘端算出的标注到后端即被丢弃。
    """

    type: str = "nav_result"
    frame_id: str
    session_id: str | None = None
    latency_ms: float | None = None
    backend: str | None = None
    labels: list[EdgeNavLabel] = []
    # === M1/M6 帧级属性（可选以兼容未升级的边缘端）===
    plane_assessment: PlaneAssessment | None = None
    image_quality: ImageQuality | None = None
    # 毫秒级 epoch（边缘端 push_nav_result/push_frame 的口径）
    timestamp_ms: int | None = None


class PingMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.PING


class PongMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.PONG


class AuthMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.AUTH
    device_id: str | None = None
    api_key: str | None = None
    session_id: str | None = None
    token: str | None = None


class AuthOKMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.AUTH_OK
    session_id: str | None = None
    token: str | None = None


class DiagnosisResultMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.DIAGNOSIS_RESULT
    request_id: str


class SessionTerminatedMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.SESSION_TERMINATED
    reason: str
    affected_organs: list[str] | None = None
    kb_version: str | None = None


class NavigationRevocationMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.NAVIGATION_REVOCATION
    frame_id: str
    reason: str


class ReconnectAckMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.RECONNECT_ACK
    session_id: str
    session_ttl_remaining_ms: int | None = None


# ============== 反馈相关模型 ==============
class FeedbackContent(BaseModel):
    field: str
    original_value: str
    corrected_value: str
    reason: str


class FeedbackSubmission(BaseModel):
    request_id: str
    session_id: str
    feedback_type: FeedbackType
    feedback_level: FeedbackLevel
    content: FeedbackContent
    reviewer_id: str | None = None
    idempotency_key: str | None = None


# ============== 知情同意相关模型 ==============
class ConsentQueryResponse(BaseModel):
    patient_id: str
    consent_status: ConsentStatus
    consented_at: datetime | None = None
    withdrawn_at: datetime | None = None
    # 无知情同意记录时接口返回 None（表示"版本未知"），不能回填成假的默认版本号
    consent_version: str | None = None


class ConsentAction(str, Enum):
    GRANT = "grant"
    WITHDRAW = "withdraw"


class ConsentUpdateRequest(BaseModel):
    patient_id: str
    action: ConsentAction
    operator_id: str
    consent_version: str = "1.0"


class ConsentUpdateResponse(BaseModel):
    patient_id: str
    consent_status: ConsentStatus
    updated_at: datetime


# ============== 会话管理相关模型 ==============
class SessionCreateRequest(BaseModel):
    patient_id: str
    device_id: str
    target_organs: list[str]


class SessionResponse(BaseModel):
    session_id: str
    patient_id: str
    device_id: str
    status: SessionStatus
    target_organs: list[str]
    organ_statuses: dict[str, str]
    knowledge_base_version: str
    created_at: datetime
    updated_at: datetime
    consent_status_at_creation: ConsentStatus
    ttl_remaining_seconds: int | None = None
    # 详情端点补充的脱敏人口学（A.36 P0：工作区身份必须数据驱动）。
    # 脱敏在服务端完成（姓+**），前端不得猜姓名；缺数据 = None。
    patient_name_masked: str | None = None
    gender: str | None = None
    age: int | None = None


class SessionListItem(BaseModel):
    """工作列表单行 —— 对应设计稿 01_工作列表 的一行。

    脱敏纪律：这里只出**已脱敏**的 `patient_name_masked`（姓+**），
    不出完整姓名、不出生日期 —— 列表是共享屏幕，暴露全名即构成泄露面。
    年龄由服务端从 birth_date 派生，避免把生日发到前端再算。
    """
    session_id: str
    patient_id: str
    patient_name_masked: str | None = None
    gender: str | None = None
    age: int | None = None
    status: SessionStatus
    target_organs: list[str] = []
    created_at: datetime
    updated_at: datetime
    ttl_remaining_seconds: int | None = None


class SessionListResponse(BaseModel):
    """工作列表整体。分组在服务端做完，前端不再二次分类。

    契约 §0 P2「一次决策」：列表页医生此刻只需要挑一个病人开始，
    所以只给两组 —— 进行中 / 今日待处理，不按七种状态切页。
    """
    in_progress: list[SessionListItem] = []
    pending_today: list[SessionListItem] = []
    total_pending: int = 0

    @property
    def items(self) -> list[SessionListItem]:
        return self.in_progress + self.pending_today


class SessionUpdateRequest(BaseModel):
    status: SessionStatus | None = None
    organ_statuses: dict[str, str] | None = None


# ============== T-GB-AGT-01 多器官联合诊断 ==============
class OrganDiagnosisStatus(BaseModel):
    """单器官在联合诊断中的执行结果。"""

    organ: str
    status: OrganStatus
    request_id: str | None = None
    confidence: float = 0.0
    elapsed_ms: float = 0.0
    reason: str | None = None


class MultiOrganDiagnosisRequest(BaseModel):
    """会话级多器官联合诊断请求。

    ``organs`` 缺省时取会话 ``target_organs``；超时按规格默认
    单器官 30s / 全局 90s（可显式覆盖，用于测试与调参）。
    """

    session_id: str
    organs: list[str] | None = None
    response_tier: ResponseTier | None = None
    per_organ_timeout_seconds: float | None = None
    total_timeout_seconds: float | None = None


class MultiOrganDiagnosisResponse(BaseModel):
    """多器官联合诊断汇总响应（含"部分完成"标记）。"""

    session_id: str
    session_status: SessionStatus
    organs: list[str]
    organ_statuses: dict[str, OrganStatus]
    organ_results: list[OrganDiagnosisStatus]
    completed_organs: list[str]
    failed_organs: list[str]
    skipped_organs: list[str]
    needs_review_organs: list[str]
    partial: bool
    total_ms: float


# ============== T-GB-AGT-02 档位升级的医生确认 ==============
class RoutingConfirmationRequest(BaseModel):
    """医生对"是否升级到全量路径"的回答。

    铁律：升级决策权交医生。只有"多特征组合推理"会走到本接口；
    跨器官 / 置信度不足两类情形按规格直接走全量路径，不产生待确认项。
    """

    confirmed: bool
    reason: str | None = None


class RoutingConfirmationResponse(BaseModel):
    """确认结果（含是否已按全量档位重跑）。"""

    request_id: str
    confirmed: bool
    effective_tier: ResponseTier
    requires_confirmation: bool
    rerun_enqueued: bool
    routing: dict[str, Any]


# ============== T-GB-API-02 报告生成与签发 ==============
class ReportDraftRequest(BaseModel):
    """生成报告草稿（可选一步签名/推送）。"""

    request_id: str
    sign: bool = False
    push: bool = False
    operator_id: str | None = None


class ReportResponse(BaseModel):
    """报告视图（草稿 / 已签名 / 已推送）。"""

    report_id: str
    request_id: str
    session_id: str | None = None
    patient_id: str | None = None
    organ: str | None = None
    status: str
    template_version: str | None = None
    content_digest: str | None = None
    signature_present: bool = False
    signature_verified: bool = False
    signing_key_name: str | None = None
    signed_at: datetime | None = None
    signed_by: str | None = None
    pacs_adapter: str | None = None
    pacs_status: str | None = None
    pacs_message: str | None = None
    pacs_attempts: int | None = None
    pushed_at: datetime | None = None
    created_at: datetime | None = None
    content: dict[str, Any] | None = None
    rendered: str | None = None
    # M8 完整性校验：{organ, template, required, missing, passed}
    completeness: dict[str, Any] | None = None


class ReportSignRequest(BaseModel):
    """签发报告（可选覆盖操作者标识）。"""

    operator_id: str | None = None


class ReportPushRequest(BaseModel):
    """推送报告到 PACS/HIS（可指定适配器名，缺省用配置值）。"""

    adapter: str | None = None


# ============== 安全事件相关模型 ==============
class SafetyEventType(str, Enum):
    KNOWLEDGE_UPDATED = "KnowledgeUpdated"
    CONSENT_WITHDRAWN = "ConsentWithdrawn"
    DEVICE_ISOLATED = "DeviceIsolated"
    HSM_DEGRADED = "HsmDegraded"


class SafetySeverity(str, Enum):
    SAFETY_CRITICAL = "safety_critical"
    IMPORTANT = "important"
    ROUTINE = "routine"


class SafetyEventCreate(BaseModel):
    event_type: SafetyEventType
    severity: SafetySeverity
    kb_version: str | None = None
    device_id: str | None = None
    patient_id: str | None = None
    payload: dict[str, Any] | None = None


# ============== 设备管理相关模型 ==============
class DeviceRegisterRequest(BaseModel):
    device_name: str
    device_type: str
    location: str
    jetpack_version: str


class DeviceCheckInResponse(BaseModel):
    """设备自通告（check-in）响应：设备侧凭 api_key 声明自己已在线。

    供给（分配 device_id/api_key）走管理员 POST /register；设备开机后
    通过 /checkin 声明上线（wiring-audit P0-4，消除假隔离）。
    """
    device_id: str
    status: str
    registered: bool = True


class DeviceHeartbeatRequest(BaseModel):
    device_id: str
    state: str = "registered"


class DeviceHeartbeatResponse(BaseModel):
    device_id: str
    status: str = "ok"
    server_time: str


class DeviceHealthProbeRequest(BaseModel):
    device_id: str


class DeviceHealthProbeResponse(BaseModel):
    device_id: str
    status: str = "ok"
    server_time: str


class DeviceRegisterResponse(BaseModel):
    device_id: str
    api_key: str
    status: str = "active"


class DeviceRecoveryResponse(BaseModel):
    device_id: str
    status: str
    recovery_token: str
    expires_at: str


class DeviceRemoteRecoveryResponse(BaseModel):
    device_id: str
    status: str
    new_api_key: str
    expires_at: str


# ============== A.51 管理面列表（设备台账 / 患者检索） ==============
class DeviceListItem(BaseModel):
    """设备台账条目——**绝不回 api_key**（哈希只在校验路径用）。

    字段名与 `DeviceListItem` 契约固定：注册响应里的 api_key 只在注册/恢复
    时刻一次性下发；列表是管理面只读视图，泄漏面必须为零。
    """

    device_id: str
    device_name: str
    device_type: str
    location: str
    status: str
    jetpack_version: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DeviceListResponse(BaseModel):
    items: list[DeviceListItem]
    total: int
    offset: int
    limit: int


class PatientListItem(BaseModel):
    """患者检索条目：FHIR Patient 表 + 最近同意状态 + 最近会话时间。"""

    patient_id: str
    name: str | None = None
    gender: str | None = None
    consent_status: str | None = None
    last_session_at: datetime | None = None


class PatientListResponse(BaseModel):
    items: list[PatientListItem]
    total: int
    offset: int
    limit: int


class DeviceDeregisterResponse(BaseModel):
    device_id: str
    status: str


class FeedbackSubmitResponse(BaseModel):
    status: str
    message: str
    request_id: str
    feedback_id: str


# ============== 语音指令相关模型 ==============
class VoiceCommandType(str, Enum):
    DIAGNOSIS_TRIGGER = "diagnosis_trigger"
    GRADE_CHANGE = "grade_change"
    NAVIGATION_CONTROL = "navigation_control"


class VoiceConfirmRequest(BaseModel):
    session_id: str
    transcript: str
    command_type: VoiceCommandType
    confirmed: bool
    timestamp: int
    organ: str | None = None
    grade_target: str | None = None
    nav_action: str | None = None


class VoiceConfirmResponse(BaseModel):
    session_id: str
    executed: bool
    action_taken: str | None = None
    request_id: str | None = None
