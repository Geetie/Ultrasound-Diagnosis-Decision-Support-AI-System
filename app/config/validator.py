from typing import List, Optional

from app.config.settings import settings

# T-G1-DATA-02：契约字段取值（与前端 types/api.ts 的联合类型保持一致）
VERSION_COMPATIBLE = "compatible"
VERSION_INCOMPATIBLE = "incompatible"


class ConfigValidationError(Exception):
    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__(f"Configuration validation failed: {', '.join(errors)}")


def validate_config() -> List[str]:
    errors: List[str] = []
    
    if not settings.database_url:
        errors.append("DATABASE_URL is required")
    
    if not settings.redis_url:
        errors.append("REDIS_URL is required")
    
    if settings.redis_pool_max_connections <= 0:
        errors.append("REDIS_POOL_MAX_CONNECTIONS must be greater than 0")
    
    if settings.redis_pool_timeout <= 0:
        errors.append("REDIS_POOL_TIMEOUT must be greater than 0")
    
    if settings.api_port < 1 or settings.api_port > 65535:
        errors.append("API_PORT must be between 1 and 65535")
    
    if settings.safety_gateway_port < 1 or settings.safety_gateway_port > 65535:
        errors.append("SAFETY_GATEWAY_PORT must be between 1 and 65535")
    
    if settings.jwt_expire_minutes <= 0:
        errors.append("JWT_EXPIRE_MINUTES must be greater than 0")
    
    if settings.jwt_refresh_expire_days <= 0:
        errors.append("JWT_REFRESH_EXPIRE_DAYS must be greater than 0")
    
    valid_jwt_algorithms = ['RS256', 'RS384', 'RS512']
    if settings.jwt_algorithm not in valid_jwt_algorithms:
        errors.append(f"JWT_ALGORITHM must be one of: {', '.join(valid_jwt_algorithms)}")
    
    valid_envs = ['development', 'staging', 'production']
    if settings.env not in valid_envs:
        errors.append(f"ENV must be one of: {', '.join(valid_envs)}")
    
    if settings.max_concurrent_sessions_per_user <= 0:
        errors.append("MAX_CONCURRENT_SESSIONS_PER_USER must be greater than 0")
    
    if settings.max_queued_requests_per_user <= 0:
        errors.append("MAX_QUEUED_REQUESTS_PER_USER must be greater than 0")
    
    if settings.safety_gateway_p99_latency_warning_threshold_ms <= 0:
        errors.append("SG_P99_LATENCY_WARNING_THRESHOLD_MS must be greater than 0")
    
    if settings.safety_gateway_p99_latency_critical_threshold_ms <= 0:
        errors.append("SG_P99_LATENCY_CRITICAL_THRESHOLD_MS must be greater than 0")
    
    if settings.safety_gateway_p99_latency_warning_threshold_ms >= settings.safety_gateway_p99_latency_critical_threshold_ms:
        errors.append("SG_P99_LATENCY_WARNING_THRESHOLD_MS must be less than SG_P99_LATENCY_CRITICAL_THRESHOLD_MS")

    if settings.consent_withdrawal_e2e_latency_target_ms <= 0:
        errors.append("CONSENT_WITHDRAWAL_E2E_LATENCY_TARGET_MS must be greater than 0")

    if settings.consent_withdrawal_e2e_latency_target_ms > settings.consent_withdrawal_poll_timeout_ms:
        errors.append(
            "CONSENT_WITHDRAWAL_E2E_LATENCY_TARGET_MS must not exceed "
            "CONSENT_WITHDRAWAL_POLL_TIMEOUT_MS (otherwise the acceptance target is "
            "unreachable by construction: one blocking read can exceed it)"
        )

    for name, value in (
        ("DRIFT_INPUT_KL_THRESHOLD", settings.drift_input_kl_threshold),
        ("DRIFT_OUTPUT_KL_THRESHOLD", settings.drift_output_kl_threshold),
        ("DRIFT_OUTPUT_MEAN_SHIFT_THRESHOLD", settings.drift_output_mean_shift_threshold),
    ):
        if value <= 0:
            errors.append(f"{name} must be greater than 0")
    if settings.drift_distribution_min_samples < 2:
        errors.append("DRIFT_DISTRIBUTION_MIN_SAMPLES must be at least 2")

    if settings.ab_experiment_enabled:
        if not 0 < settings.ab_experiment_traffic_percent <= 100:
            errors.append("AB_EXPERIMENT_TRAFFIC_PERCENT must be in (0, 100]")
        if not 0 < settings.ab_alpha < 1:
            errors.append("AB_ALPHA must be in (0, 1)")
        if not 0 < settings.ab_power < 1:
            errors.append("AB_POWER must be in (0, 1)")
        if not 0 < settings.ab_mde < 1:
            errors.append("AB_MDE must be in (0, 1)")
        if settings.ab_non_inferiority_margin <= 0:
            errors.append("AB_NON_INFERIORITY_MARGIN must be greater than 0")
        if settings.ab_min_samples_per_variant < 1:
            errors.append("AB_MIN_SAMPLES_PER_VARIANT must be at least 1")

    if settings.correction_rate_window_days <= 0:
        errors.append("CORRECTION_RATE_WINDOW_DAYS must be greater than 0")
    if settings.correction_rate_min_samples < 1:
        errors.append("CORRECTION_RATE_MIN_SAMPLES must be at least 1")
    if not 0 < settings.correction_rate_direction_alert_threshold <= 1:
        errors.append("CORRECTION_RATE_DIRECTION_ALERT_THRESHOLD must be in (0, 1]")
    if not 0 < settings.correction_rate_reviewer_alert_threshold <= 1:
        errors.append("CORRECTION_RATE_REVIEWER_ALERT_THRESHOLD must be in (0, 1]")
    if not 0 <= settings.correction_rate_peer_baseline_threshold < 1:
        errors.append("CORRECTION_RATE_PEER_BASELINE_THRESHOLD must be in [0, 1)")

    if settings.rollout_enabled:
        if not 0 <= settings.rollout_full_percent <= 100:
            errors.append("ROLLOUT_FULL_PERCENT must be between 0 and 100")
        if settings.rollout_compact_session_threshold < 1:
            errors.append("ROLLOUT_COMPACT_SESSION_THRESHOLD must be at least 1")
        if settings.rollout_counter_ttl_seconds <= 0:
            errors.append("ROLLOUT_COUNTER_TTL_SECONDS must be greater than 0")

    errors.extend(validate_version_compatibility())

    return errors


def validate_version_compatibility(
    model_version: Optional[str] = None,
    kb_version: Optional[str] = None,
) -> List[str]:
    """T-G1-DATA-02：模型版本与知识库版本的兼容性**强制校验**。

    兼容矩阵由 :func:`app.knowledge.version.is_compatible` 唯一定义：
    model v1 -> kb v1/v2（v2 为降级兼容）、model v2 -> kb v2。

    升级顺序要求 **知识库先于模型**：先升模型会让新模型配旧知识库（model v2 +
    kb v1）直接落到不兼容区。因此这里在进程启动时即判定，不兼容则拒绝启动，
    而不是等到某次诊断请求才暴露——那时已经有医生在看结果了。
    """
    model = settings.phase0a_mock_model_version if model_version is None else model_version
    kb = settings.phase0a_mock_kb_version if kb_version is None else kb_version

    from app.knowledge.version import is_compatible

    if not is_compatible(model, kb):
        return [
            f"VERSION_COMPATIBILITY: model '{model}' is not compatible with "
            f"kb '{kb}' (upgrade order requires KB before model)"
        ]

    return []


def check_version_compatibility(
    model_version: Optional[str] = None,
    kb_version: Optional[str] = None,
) -> str:
    """返回诊断结果契约字段 ``version_compatibility`` 的真实取值。

    之前该字段在响应组装处被硬编码为 ``"compatible"``，无论实际版本对是否兼容
    都对外宣称兼容——属于"看起来通过了校验"的假契约。此处提供唯一取值来源。
    """
    errors = validate_version_compatibility(model_version, kb_version)
    return VERSION_INCOMPATIBLE if errors else VERSION_COMPATIBLE


def validate_and_exit_on_error() -> None:
    errors = validate_config()
    if errors:
        raise ConfigValidationError(errors)