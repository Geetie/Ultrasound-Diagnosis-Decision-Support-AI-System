# NOTE: 速率限制相关键名（ultrasound:ratelimit:* 和 ultrasound:ratelimit:dedup:*）
# 当前定义在 app/config/rate_limit.py 中，应迁移至本文件以实现集中管理。
from app.config.settings import settings

_PREFIX = settings.redis_key_prefix


class RedisKey:
    """Redis 键名常量定义 - 统一命名规范"""

    # 认证相关键名
    @staticmethod
    def auth_token(token_id: str) -> str:
        return f"{_PREFIX}:auth:token:{token_id}"

    @staticmethod
    def auth_token_family(token_id: str) -> str:
        return f"{_PREFIX}:auth:token:family:{token_id}"

    @staticmethod
    def auth_user_tokens(user_id: str) -> str:
        return f"{_PREFIX}:auth:user:tokens:{user_id}"

    @staticmethod
    def auth_family_tokens(family_id: str) -> str:
        return f"{_PREFIX}:auth:family:tokens:{family_id}"

    @staticmethod
    def auth_family_revoked(family_id: str) -> str:
        return f"{_PREFIX}:auth:family:revoked:{family_id}"

    AUTH_TOKEN_PATTERN = f"{_PREFIX}:auth:token:*"

    # 同意相关键名
    CONSENT_WITHDRAWAL_STREAM = f"{_PREFIX}:consent:withdrawal:stream"
    CONSENT_WITHDRAWAL_DEAD_LETTER_STREAM = f"{_PREFIX}:consent:withdrawal:dead_letter"
    CONSENT_WITHDRAWAL_NOTIFICATION = f"{_PREFIX}:consent:withdrawal:notification"

    @staticmethod
    def consent_withdrawn_session(session_id: str) -> str:
        return f"{_PREFIX}:consent:withdrawn:session:{session_id}"

    # Edge 相关键名
    @staticmethod
    def edge_session(session_id: str, patient_id: str) -> str:
        return f"{_PREFIX}:edge:session:{session_id}:{patient_id}"

    @staticmethod
    def edge_session_pattern(patient_id: str) -> str:
        return f"{_PREFIX}:edge:session:*:{patient_id}"

    @staticmethod
    def edge_stop_ai_annotation(session_id: str) -> str:
        return f"{_PREFIX}:edge:stop_ai_annotation:{session_id}"

    # 脱敏相关键名
    @staticmethod
    def desens_session(session_id: str, patient_id: str) -> str:
        return f"{_PREFIX}:desens:session:{session_id}:{patient_id}"

    @staticmethod
    def desens_restore_rate(hour_key: str) -> str:
        return f"{_PREFIX}:desens:restore_rate:{hour_key}"

    DESSENS_RULES_UPDATE = f"{_PREFIX}:desens:rules:update"
    DESSENS_HMAC_KEY = f"{_PREFIX}:desens:hmac_key"
    DESSENS_TOKEN_COUNTER = f"{_PREFIX}:desens:token_counter"
    DESSENS_TOKEN_MAPPING = f"{_PREFIX}:desens:token_mapping"

    # 诊断幂等性键名
    @staticmethod
    def diagnosis_idempotency(key: str) -> str:
        return f"{_PREFIX}:diagnosis:idempotency:{key}"

    @staticmethod
    def diagnosis_result(request_id: str) -> str:
        return f"{_PREFIX}:diagnosis:result:{request_id}"

    @staticmethod
    def feedback_idempotency(key: str) -> str:
        return f"{_PREFIX}:feedback:idempotency:{key}"

    @staticmethod
    def custom_idempotency(prefix: str, key: str) -> str:
        return f"{_PREFIX}:{prefix}:idempotency:{key}"

    # HSM 相关键名
    @staticmethod
    def hsm_desens_aes_key(key_version: int) -> str:
        return f"{_PREFIX}:hsm:desens_aes_key:{key_version}"

    HSM_DESENS_AES_KEY_VERSION = f"{_PREFIX}:hsm:desens_aes_key_version"

    @staticmethod
    def hsm_key(slot: str, key_name: str) -> str:
        return f"{_PREFIX}:hsm:keys:{slot}:{key_name}"

    @staticmethod
    def hsm_key_private(slot: str, key_name: str) -> str:
        return f"{_PREFIX}:hsm:keys:{slot}:{key_name}:private"

    HSM_DEGRADED = f"{_PREFIX}:hsm:degraded"
    HSM_DEGRADED_AT = f"{_PREFIX}:hsm:degraded_at"
    HSM_DEGRADED_REASON = f"{_PREFIX}:hsm:degraded_reason"

    @staticmethod
    def hsm_emergency_signing(field: str) -> str:
        return f"{_PREFIX}:hsm:emergency_signing:{field}"

    @staticmethod
    def hsm_emergency_daily_count(date_key: str) -> str:
        return f"{_PREFIX}:hsm:emergency_signing:daily_count:{date_key}"

    @staticmethod
    def hsm_emergency_pending_verification(token: str) -> str:
        return f"{_PREFIX}:hsm:emergency_pending_verification:{token}"

    HSM_KEYS_PATTERN = f"{_PREFIX}:hsm:keys:*:private"

    # 安全网关相关键名
    SG_THRESHOLD_UPDATE = f"{_PREFIX}:sg:threshold:update"

    # 设备相关键名
    @staticmethod
    def device_recovery_token(device_id: str) -> str:
        return f"{_PREFIX}:device:recovery_token:{device_id}"


class LegacyRedisKey:
    """旧版键名常量 - 用于双写兼容阶段"""

    @staticmethod
    def auth_token(token_id: str) -> str:
        return f"refresh_token:{token_id}"

    @staticmethod
    def auth_token_family(token_id: str) -> str:
        return f"token_family:{token_id}"

    @staticmethod
    def auth_user_tokens(user_id: str) -> str:
        return f"user_tokens:{user_id}"

    @staticmethod
    def auth_family_tokens(family_id: str) -> str:
        return f"family_tokens:{family_id}"

    AUTH_TOKEN_PATTERN = "refresh_token:*"

    CONSENT_WITHDRAWAL_STREAM = "consent:withdrawal"
    CONSENT_WITHDRAWAL_DEAD_LETTER_STREAM = "consent:withdrawal:dead_letter"
    CONSENT_WITHDRAWAL_NOTIFICATION = "consent:withdrawal:notification"

    @staticmethod
    def consent_withdrawn_session(session_id: str) -> str:
        return f"consent:withdrawn:session:{session_id}"

    @staticmethod
    def edge_session(session_id: str, patient_id: str) -> str:
        return f"edge:session:{session_id}:{patient_id}"

    @staticmethod
    def edge_stop_ai_annotation(session_id: str) -> str:
        return f"edge:stop_ai_annotation:{session_id}"

    @staticmethod
    def desens_session(session_id: str, patient_id: str) -> str:
        return f"desens:session:{session_id}:{patient_id}"

    @staticmethod
    def desens_restore_rate(hour_key: str) -> str:
        return f"desens:restore_rate:{hour_key}"

    @staticmethod
    def hsm_desens_aes_key(key_version: int) -> str:
        return f"hsm:desens_aes_key:{key_version}"

    HSM_DESENS_AES_KEY_VERSION = "hsm:desens_aes_key_version"

    @staticmethod
    def hsm_key(slot: str, key_name: str) -> str:
        return f"hsm:keys:{slot}:{key_name}"

    @staticmethod
    def hsm_key_private(slot: str, key_name: str) -> str:
        return f"hsm:keys:{slot}:{key_name}:private"

    HSM_DEGRADED = "hsm:degraded"
    HSM_DEGRADED_AT = "hsm:degraded_at"
    HSM_DEGRADED_REASON = "hsm:degraded_reason"

    @staticmethod
    def hsm_emergency_signing(field: str) -> str:
        return f"hsm:emergency_signing:{field}"

    @staticmethod
    def hsm_emergency_daily_count(date_key: str) -> str:
        return f"hsm:emergency_signing:daily_count:{date_key}"

    HSM_KEYS_PATTERN = "hsm:keys:*:private"

    SG_THRESHOLD_UPDATE = "sg:threshold:update"
