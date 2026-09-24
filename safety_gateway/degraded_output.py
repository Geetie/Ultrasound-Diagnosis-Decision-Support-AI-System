from typing import Any, ClassVar

import structlog

from safety_gateway.desensitization import sg_desensitization_engine
from safety_gateway.validation_result import DegradedReason

logger = structlog.get_logger()

MAX_RECURSION_DEPTH = 20

# 口径必须与 edge/safety.py、safety_gateway/sg_config.py、
# safety_gateway/checker/orchestrator.py 保持一致（一致性测试见
# tests/unit/edg/test_whitelist_consistency.py）。
DEFAULT_WHITELIST_FIELDS: list[str] = [
    "organ_name",
    "anatomical_location",
    "echogenicity",
    "boundary",
    "morphology",
    "vascularity",
    "calcification",
    "lesion_size_measurements",
    "observations",
    # M1 / M6 / M2 / M9 / M3（详见 edge/safety.py 注释）
    "plane_assessment",
    "image_quality",
    "risk_flag",
    "suggested_action",
    "posterior_acoustic",
    "measurement",
    "aspect_ratio",
    "distance_to_capsule",
    "distance_to_organ_edge",
    "distance_to_skin",
    "volume",
]

_WHITELIST_FIELD_MAPPING: dict[str, str] = {}

FORBIDDEN_GRADING_TERMS: ClassVar[tuple[str, ...]] = (
    "高度怀疑", "低度怀疑",
    "TI-RADS", "BI-RADS", "LI-RADS",
    "恶性", "良性", "可疑", "分级",
    "ACR",
)

NEGATION_PREFIXES: ClassVar[tuple[str, ...]] = (
    "非", "无", "未", "不",
)


def filter_forbidden_terms(value: Any, key: str = "", depth: int = 0) -> Any:
    if depth >= MAX_RECURSION_DEPTH:
        logger.warning("sg_filter_forbidden_terms_depth_exceeded", depth=depth)
        return value
    if isinstance(value, str):
        for term in FORBIDDEN_GRADING_TERMS:
            idx = 0
            while True:
                pos = value.find(term, idx)
                if pos == -1:
                    break
                if pos > 0 and value[pos - 1] in NEGATION_PREFIXES:
                    idx = pos + len(term)
                    continue
                logger.warning(
                    "sg_forbidden_grading_term_filtered",
                    key=key,
                    term=term,
                )
                value = value[:pos] + "***" + value[pos + len(term):]
                idx = pos + 3
        return value
    elif isinstance(value, dict):
        return {k: filter_forbidden_terms(v, key=k, depth=depth + 1) for k, v in value.items()}
    elif isinstance(value, list):
        return [filter_forbidden_terms(item, key=key, depth=depth + 1) for item in value]
    return value


_DEGRADED_MARKS: dict[DegradedReason, dict[str, Any]] = {
    DegradedReason.SENSITIVE_DATA_DETECTED: {
        "sg_output_degraded": True,
    },
    DegradedReason.DB_TIMEOUT: {
        "db_timeout": True,
        "error_code": "E4005B",
    },
    DegradedReason.CONTRAINDICATION_HIT: {
        "contraindication_hit": True,
    },
    DegradedReason.CONFIDENCE_BELOW_THRESHOLD: {
        "confidence_below_threshold": True,
    },
    DegradedReason.FUSION_UNAVAILABLE: {
        "fusion_bypassed": True,
    },
    DegradedReason.COMBINED_FAILURE: {
        "fusion_bypassed": True,
        "combined_failure": True,
        "error_code": "E4005C",
    },
    DegradedReason.EDGE_DISCONNECTED: {
        "edge_disconnected": True,
    },
    DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED: {
        "fusion_degraded": True,
        "fusion_unavailable": True,
        "fusion_unavailable_sensitive_detected": True,
        "error_code": "E4005A1",
    },
    DegradedReason.DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED: {
        "fusion_degraded": True,
        "desensitization_degraded_sensitive_detected": True,
        "error_code": "E4005A1",
    },
    DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN: {
        "safety_gateway_unavailable": True,
        "sg_unavailable_consent_withdrawn": True,
        "error_code": "E4005A2",
    },
    DegradedReason.CONSENT_STATUS_UNKNOWN: {
        "safety_gateway_unavailable": True,
        "consent_status_unknown": True,
        "error_code": "E4005A2",
    },
    DegradedReason.DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE: {
        "fusion_degraded": True,
        "safety_gateway_unavailable": True,
        "conclusion_suppressed": True,
        "error_code": "E4005C",
    },
    DegradedReason.CONSISTENCY_CONTRADICTORY: {
        "consistency_contradictory": True,
        "conclusion_suppressed": True,
        "requires_manual_review": True,
        "error_code": "E3006",
    },
    DegradedReason.CONSISTENCY_PARTIAL: {
        "consistency_partial": True,
        "conclusion_suppressed": True,
        "error_code": "E3006",
    },
}

_DEFAULT_MARKS: dict[str, Any] = {"fusion_degraded": False, "safety_gateway_unavailable": False}

_DEFENSIVE_BRANCH_REASONS = {
    DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED,
    DegradedReason.DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED,
    DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN,
    DegradedReason.CONSENT_STATUS_UNKNOWN,
    DegradedReason.DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE,
}

_DUAL_DEGRADATION_REASONS: frozenset[DegradedReason] = frozenset({
    DegradedReason.DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE,
    DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED,
    DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN,
})

_DEGRADATION_WARNINGS: dict[DegradedReason, str] = {
    DegradedReason.DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE: "双重安全校验缺失，请人工核验",
    DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED: "双重安全校验缺失，请人工核验",
    DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN: "双重安全校验缺失，请人工核验",
}


_CONCLUSION_SUPPRESSED_REASONS: frozenset[DegradedReason] = frozenset({
    DegradedReason.SENSITIVE_DATA_DETECTED,
    DegradedReason.CONTRAINDICATION_HIT,
    DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED,
    DegradedReason.DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED,
    DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN,
    DegradedReason.CONSENT_STATUS_UNKNOWN,
    DegradedReason.DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE,
    DegradedReason.COMBINED_FAILURE,
})


def apply_degraded_output(
    data: dict[str, Any],
    reason: DegradedReason = DegradedReason.SENSITIVE_DATA_DETECTED,
    whitelist_fields: list[str] | None = None,
    fusion_available: bool | None = None,
) -> dict[str, Any]:
    effective_whitelist = whitelist_fields or DEFAULT_WHITELIST_FIELDS
    degraded_data = {}

    if reason in _CONCLUSION_SUPPRESSED_REASONS:
        data = {k: v for k, v in data.items() if k != "diagnostic_conclusion"}
    else:
        if "diagnostic_conclusion" in data:
            data = {**data, "diagnostic_conclusion": None}

    for key, value in data.items():
        if key in effective_whitelist:
            if value is None:
                degraded_data[key] = None
            elif isinstance(value, str):
                degraded_data[key] = sg_desensitization_engine.desensitize(value)
            elif isinstance(value, dict):
                degraded_data[key] = sg_desensitization_engine.desensitize_dict(value)
            elif isinstance(value, list):
                processed = []
                for item in value:
                    if isinstance(item, str):
                        result = sg_desensitization_engine.desensitize(item)
                        if result is not None:
                            processed.append(result)
                    elif isinstance(item, dict):
                        processed.append(sg_desensitization_engine.desensitize_dict(item))
                    elif isinstance(item, bool) or isinstance(item, (int, float)):
                        processed.append(item)
                    elif item is not None:
                        logger.warning("sg_degraded_output_unknown_list_item_type", key=key, type=type(item).__name__)
                degraded_data[key] = processed
            else:
                degraded_data[key] = value

    for key, value in list(degraded_data.items()):
        degraded_data[key] = filter_forbidden_terms(value, key=key)

    degraded_data["conclusion_suppressed"] = reason in _CONCLUSION_SUPPRESSED_REASONS or data.get("diagnostic_conclusion") is None
    degraded_data["degraded_fields"] = [k for k in data if k not in effective_whitelist]

    marks = {**_DEFAULT_MARKS, **_DEGRADED_MARKS.get(reason, {})}
    if reason == DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN:
        marks["fusion_degraded"] = not fusion_available if fusion_available is not None else False
    if reason in _DEFENSIVE_BRANCH_REASONS:
        logger.info("sg_degraded_output_defensive_branch", reason=reason.value)
    degraded_data.update(marks)

    degraded_data["degraded_reason"] = reason.value
    degraded_data["degradation_warning"] = _DEGRADATION_WARNINGS.get(
        reason, "脱敏降级模式，身份信息保护等级降低"
    )

    return degraded_data
