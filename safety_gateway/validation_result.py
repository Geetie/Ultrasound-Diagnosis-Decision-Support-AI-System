"""Safety Gateway校验结果模型模块

定义SG安全网关的校验结果数据结构和相关类型。
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict


# CONSISTENCY: Must match app.models.schemas.ConsentStatus exactly. Any change must be reflected in both modules.
class ConsentStatus(str, Enum):
    GRANTED = "granted"
    DENIED = "denied"
    WITHDRAWN = "withdrawn"
    PENDING = "pending"


class CheckResultStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    DEGRADED = "degraded"


class DegradedReason(str, Enum):
    SENSITIVE_DATA_DETECTED = "sensitive_data_detected"
    DB_TIMEOUT = "db_timeout"
    CONTRAINDICATION_HIT = "contraindication_hit"
    CONFIDENCE_BELOW_THRESHOLD = "confidence_below_threshold"
    FUSION_UNAVAILABLE = "fusion_unavailable"
    COMBINED_FAILURE = "combined_failure"
    EDGE_DISCONNECTED = "edge_disconnected"
    FUSION_UNAVAILABLE_SENSITIVE_DETECTED = "fusion_unavailable_sensitive_detected"
    DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED = "desensitization_degraded_sensitive_detected"
    SG_UNAVAILABLE_CONSENT_WITHDRAWN = "sg_unavailable_consent_withdrawn"
    DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE = "dual_degradation_fusion_and_sg_unavailable"
    CONSENT_STATUS_UNKNOWN = "consent_status_unknown"
    CONSISTENCY_CONTRADICTORY = "consistency_contradictory"
    CONSISTENCY_PARTIAL = "consistency_partial"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    HEALTH_CHECK_FAILURE = "health_check_failure"
    MEMORY_PRESSURE = "memory_pressure"


class ThresholdCheckResult(BaseModel):
    """阈值校验结果"""
    organ: str
    confidence: float
    threshold: float
    passed: bool
    message: str


class CheckResult(BaseModel):
    status: CheckResultStatus
    consent_status: ConsentStatus | None = None
    sensitive_data_detected: list[str] = []
    contraindication_matches: list[dict[str, Any]] = []
    threshold_results: list[dict[str, Any]] = []
    message: str = ""
    degraded_mode: bool = False
    degraded_reason: DegradedReason | None = None
    elapsed_ms: float = 0.0
    consent_check_timeout: bool = False
    hallucination_warning: bool = False
    consent_status_source: str | None = None
    requires_manual_review: bool = False
    safety_gateway_unavailable: bool = False
    error_code: str | None = None

    model_config = ConfigDict(use_enum_values=True)

    @classmethod
    def failed(
        cls,
        consent_status: ConsentStatus | None,
        message: str,
        elapsed_ms: float,
        *,
        sensitive_data_detected: list[str] | None = None,
        degraded_mode: bool = False,
        degraded_reason: DegradedReason | None = None,
        consent_check_timeout: bool = False,
        consent_status_source: str | None = None,
        requires_manual_review: bool = False,
        safety_gateway_unavailable: bool = False,
        error_code: str | None = None,
    ) -> "CheckResult":
        return cls(
            status=CheckResultStatus.FAILED,
            consent_status=consent_status,
            sensitive_data_detected=sensitive_data_detected or [],
            contraindication_matches=[],
            threshold_results=[],
            message=message,
            degraded_mode=degraded_mode,
            degraded_reason=degraded_reason,
            elapsed_ms=elapsed_ms,
            consent_check_timeout=consent_check_timeout,
            consent_status_source=consent_status_source,
            requires_manual_review=requires_manual_review,
            safety_gateway_unavailable=safety_gateway_unavailable,
            error_code=error_code,
        )

    @classmethod
    def degraded(
        cls,
        consent_status: ConsentStatus | None,
        message: str,
        degraded_reason: DegradedReason,
        elapsed_ms: float,
        *,
        sensitive_data_detected: list[str] | None = None,
        contraindication_matches: list[dict[str, Any]] | None = None,
        threshold_results: list[dict[str, Any]] | None = None,
        consent_check_timeout: bool = False,
        consent_status_source: str | None = None,
        requires_manual_review: bool = False,
        safety_gateway_unavailable: bool = False,
        error_code: str | None = None,
    ) -> "CheckResult":
        return cls(
            status=CheckResultStatus.DEGRADED,
            consent_status=consent_status,
            sensitive_data_detected=sensitive_data_detected or [],
            contraindication_matches=contraindication_matches or [],
            threshold_results=threshold_results or [],
            message=message,
            degraded_mode=True,
            degraded_reason=degraded_reason,
            elapsed_ms=elapsed_ms,
            consent_check_timeout=consent_check_timeout,
            consent_status_source=consent_status_source,
            requires_manual_review=requires_manual_review,
            safety_gateway_unavailable=safety_gateway_unavailable,
            error_code=error_code,
        )

    @classmethod
    def passed(
        cls,
        consent_status: ConsentStatus | None,
        message: str,
        elapsed_ms: float,
        *,
        threshold_results: list[dict[str, Any]] | None = None,
        consent_check_timeout: bool = False,
        consent_status_source: str | None = None,
    ) -> "CheckResult":
        return cls(
            status=CheckResultStatus.PASSED,
            consent_status=consent_status,
            sensitive_data_detected=[],
            contraindication_matches=[],
            threshold_results=threshold_results or [],
            message=message,
            degraded_mode=False,
            degraded_reason=None,
            elapsed_ms=elapsed_ms,
            consent_check_timeout=consent_check_timeout,
            consent_status_source=consent_status_source,
        )


class ConsentCheckResultSummary(BaseModel):
    """同意状态校验结果摘要"""
    status: str | None
    timeout: bool = False
    error: bool = False
    message: str = ""


class DesensitizationCheckResult(BaseModel):
    """脱敏校验结果"""
    detected_types: list[str] = []
    passed: bool = True


class ContraindicationCheckResult(BaseModel):
    """禁忌校验结果"""
    has_contraindication: bool = False
    contraindications: list[dict[str, Any]] = []


class ConsistencyCheckResult(BaseModel):
    """一致性校验结果"""
    consistent: bool = True
    conflicts: list[dict[str, Any]] = []


class ValidationSummary(BaseModel):
    """校验汇总结果"""
    overall_status: CheckResultStatus
    consent_check: ConsentCheckResultSummary | None = None
    desensitization_check: DesensitizationCheckResult | None = None
    contraindication_check: ContraindicationCheckResult | None = None
    threshold_check: list[ThresholdCheckResult] | None = None
    consistency_check: ConsistencyCheckResult | None = None
    elapsed_ms: float = 0.0
    degraded_mode: bool = False
    degraded_reason: DegradedReason | None = None


class ConsistencyLevel(str, Enum):
    """一致性校验4态判定（对齐01-系统架构设计§3.5）

    判定优先级: evidence_insufficient > contradictory > partial_consistent > consistent
    """
    CONSISTENT = "consistent"
    PARTIAL_CONSISTENT = "partial_consistent"
    CONTRADICTORY = "contradictory"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
