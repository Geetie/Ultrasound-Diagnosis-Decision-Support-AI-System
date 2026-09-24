from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


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


# ============== 基础模型 ==============
class PatientContext(BaseModel):
    age: Optional[int] = Field(None, ge=0, le=150)
    gender: Optional[str] = None
    relevant_history: Optional[str] = None


class BaseResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None


# ============== 错误响应 ==============
class ErrorResponse(BaseModel):
    error_code: str = Field(..., description="错误码，如 E1001、E2001 等")
    error_message: str = Field(..., description="错误描述")
    detail: Optional[str] = None
    request_id: Optional[str] = None
    suggestion: Optional[str] = None
    retryable: bool = False
    retry_strategy: RetryStrategyType = RetryStrategyType.NO_RETRY


# ============== 诊断相关模型 ==============
class DiagnosisRequest(BaseModel):
    session_id: str
    image_ids: List[str]
    clinical_text: Optional[str] = None
    voice_text: Optional[str] = None
    target_organ: Organ
    response_tier: ResponseTier = ResponseTier.FULL
    patient_context: Optional[PatientContext] = None
    retry: bool = False
    retry_strategy: Optional[RetryStrategy] = None
    idempotency_key: Optional[str] = None


class DiagnosisRequestResponse(BaseModel):
    request_id: str
    session_id: str
    status: str = "processing"
    estimated_time_ms: int = 3000
    response_tier: ResponseTier


class DiagnosisEvidence(BaseModel):
    knowledge_ids: Optional[List[str]] = None
    content: Optional[Dict[str, Any]] = None


class DiagnosisQualityControl(BaseModel):
    confidence_score: Optional[float] = None
    self_rag_passed: bool = False
    consistency_check_passed: bool = False


class DiagnosisResult(BaseModel):
    request_id: str
    status: str
    consistency_flag: Optional[ConsistencyFlag] = None
    desensitization_status: DesensitizationStatus
    consent_status_at_output: ConsentStatus
    fusion_status: FusionStatus
    diagnosis: Dict[str, Any]
    evidence: Optional[DiagnosisEvidence] = None
    quality_control: Optional[DiagnosisQualityControl] = None
    requires_manual_review: bool = False
    review_reason: Optional[str] = None
    ai_diagnosis_failed: bool = False
    hallucination_severity: Optional[HallucinationSeverity] = None
    version_compatibility: str = "compatible"
    processing_time_ms: int
    model_version: Optional[str] = None
    knowledge_base_version: Optional[str] = None


# ============== DICOM 相关模型 ==============
class DICOMUploadResponse(BaseModel):
    image_id: str
    session_id: str
    metadata: Dict[str, Any]
    status: str = "accepted"


class DICOMStreamRequest(BaseModel):
    session_id: str
    frame_data: str
    frame_index: int
    is_key_frame: bool = True
    priority: int = 1


class DICOMStreamResponse(BaseModel):
    session_id: str
    frame_id: str
    navigation_hint: Optional[Dict[str, Any]] = None
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
    timestamp: Optional[int] = None


class FrameMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.FRAME
    frame_id: str
    frame_index: int


class NavigationAnnotation(BaseModel):
    label: str
    confidence: float
    threshold: Optional[float] = None
    bbox: Optional[List[int]] = None


class NavigationMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.NAVIGATION
    frame_id: str
    annotations: List[NavigationAnnotation] = []
    desensitization_status: DesensitizationStatus = DesensitizationStatus.FULL


class PingMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.PING


class PongMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.PONG


class AuthMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.AUTH
    device_id: Optional[str] = None
    api_key: Optional[str] = None
    session_id: Optional[str] = None
    token: Optional[str] = None


class AuthOKMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.AUTH_OK
    session_id: Optional[str] = None
    token: Optional[str] = None


class DiagnosisResultMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.DIAGNOSIS_RESULT
    request_id: str


class SessionTerminatedMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.SESSION_TERMINATED
    reason: str
    affected_organs: Optional[List[str]] = None
    kb_version: Optional[str] = None


class NavigationRevocationMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.NAVIGATION_REVOCATION
    frame_id: str
    reason: str


class ReconnectAckMessage(BaseWebSocketMessage):
    type: WebSocketMessageType = WebSocketMessageType.RECONNECT_ACK
    session_id: str
    session_ttl_remaining_ms: Optional[int] = None


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
    reviewer_id: Optional[str] = None
    idempotency_key: Optional[str] = None


# ============== 知情同意相关模型 ==============
class ConsentQueryResponse(BaseModel):
    patient_id: str
    consent_status: ConsentStatus
    consented_at: Optional[datetime] = None
    withdrawn_at: Optional[datetime] = None
    # 无知情同意记录时接口返回 None（表示"版本未知"），不能回填成假的默认版本号
    consent_version: Optional[str] = None


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
    target_organs: List[str]


class SessionResponse(BaseModel):
    session_id: str
    patient_id: str
    device_id: str
    status: SessionStatus
    target_organs: List[str]
    organ_statuses: Dict[str, str]
    knowledge_base_version: str
    created_at: datetime
    updated_at: datetime
    consent_status_at_creation: ConsentStatus
    ttl_remaining_seconds: Optional[int] = None


class SessionUpdateRequest(BaseModel):
    status: Optional[SessionStatus] = None
    organ_statuses: Optional[Dict[str, str]] = None


# ============== T-GB-AGT-01 多器官联合诊断 ==============
class OrganDiagnosisStatus(BaseModel):
    """单器官在联合诊断中的执行结果。"""

    organ: str
    status: OrganStatus
    request_id: Optional[str] = None
    confidence: float = 0.0
    elapsed_ms: float = 0.0
    reason: Optional[str] = None


class MultiOrganDiagnosisRequest(BaseModel):
    """会话级多器官联合诊断请求。

    ``organs`` 缺省时取会话 ``target_organs``；超时按规格默认
    单器官 30s / 全局 90s（可显式覆盖，用于测试与调参）。
    """

    session_id: str
    organs: Optional[List[str]] = None
    response_tier: Optional[ResponseTier] = None
    per_organ_timeout_seconds: Optional[float] = None
    total_timeout_seconds: Optional[float] = None


class MultiOrganDiagnosisResponse(BaseModel):
    """多器官联合诊断汇总响应（含"部分完成"标记）。"""

    session_id: str
    session_status: SessionStatus
    organs: List[str]
    organ_statuses: Dict[str, OrganStatus]
    organ_results: List[OrganDiagnosisStatus]
    completed_organs: List[str]
    failed_organs: List[str]
    skipped_organs: List[str]
    needs_review_organs: List[str]
    partial: bool
    total_ms: float


# ============== T-GB-AGT-02 档位升级的医生确认 ==============
class RoutingConfirmationRequest(BaseModel):
    """医生对"是否升级到全量路径"的回答。

    铁律：升级决策权交医生。只有"多特征组合推理"会走到本接口；
    跨器官 / 置信度不足两类情形按规格直接走全量路径，不产生待确认项。
    """

    confirmed: bool
    reason: Optional[str] = None


class RoutingConfirmationResponse(BaseModel):
    """确认结果（含是否已按全量档位重跑）。"""

    request_id: str
    confirmed: bool
    effective_tier: ResponseTier
    requires_confirmation: bool
    rerun_enqueued: bool
    routing: Dict[str, Any]


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
    kb_version: Optional[str] = None
    device_id: Optional[str] = None
    patient_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None


# ============== 设备管理相关模型 ==============
class DeviceRegisterRequest(BaseModel):
    device_name: str
    device_type: str
    location: str
    jetpack_version: str


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
    organ: Optional[str] = None
    grade_target: Optional[str] = None
    nav_action: Optional[str] = None


class VoiceConfirmResponse(BaseModel):
    session_id: str
    executed: bool
    action_taken: Optional[str] = None
    request_id: Optional[str] = None
