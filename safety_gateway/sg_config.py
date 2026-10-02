from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

_KNOWN_SERVICE_PORTS = {8000, 8001, 8002, 5432, 6379, 8428, 9093}


class SGSettings(BaseSettings):

    database_url: str = "postgresql+asyncpg://admin:CHANGEME@localhost:5432/ultrasound_db"
    database_url_source: str = ""
    redis_url: str = "redis://localhost:6379/1"
    redis_pool_max_connections: int = 50
    redis_pool_timeout: int = 5
    redis_ssl: bool = False

    safety_gateway_host: str = "0.0.0.0"
    safety_gateway_port: int = 9001
    safety_gateway_whitelist_fields: list[str] = [
        "organ_name",
        "anatomical_location",
        "echogenicity",
        "boundary",
        "morphology",
        "vascularity",
        "calcification",
        "lesion_size_measurements",
        "observations",
        # M1 / M6 / M2 / M9 / M3（口径与 edge/safety.py 等 3 处一致）
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
    safety_gateway_enable_degraded_mode: bool = True
    safety_gateway_queue_timeout_seconds: float = 10.0
    fusion_layer_host: str = "localhost"
    fusion_layer_port: int = 8000
    fusion_layer_health_check_timeout: float = 1.0
    fusion_layer_health_check_cache_ttl: float = 4.0

    desensitization_rules_safety_path: str = "desensitization_rules_safety.yaml"
    desensitization_rules_safety_patch_path: str = "desensitization_rules_safety_patch.yaml"
    contraindication_rules_safety_path: str = "contraindication_rules_safety.yaml"
    medical_person_names_path: str = "medical_person_names.yaml"

    # 脱敏规则热更新 Pub/Sub 频道——必须与后端发布方
    # app/config/redis_keys.py::RedisKey.DESSENS_RULES_UPDATE
    # （f"{prefix}:desens:rules:update"）一致。历史上这里硬编码了
    # 裸名 "desensitization_rules_update"，规则热更新永远收不到
    # （wiring-audit P1-1）。
    desens_rules_sync_channel: str = "ultrasound:desens:rules:update"

    db_query_timeout: int = 5
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30

    sg_db_retry_max_attempts: int = 3
    sg_db_retry_delay_base: float = 0.5
    sg_db_retry_max_total_time_seconds: float = 10.0
    sg_threshold_cache_ttl_seconds: int = 300
    sg_circuit_breaker_failure_threshold: int = 5
    sg_circuit_breaker_success_threshold: int = 3
    sg_circuit_breaker_cooldown_period: float = 30.0
    sg_p99_latency_warning_threshold_ms: float = 4.0
    sg_p99_latency_critical_threshold_ms: float = 5.0
    sg_e2e_p99_latency_warning_threshold_ms: float = 40.0
    sg_e2e_p99_latency_critical_threshold_ms: float = 50.0
    sg_memory_budget_mb: int = 512
    sg_default_unknown_organ_threshold: float = 0.95
    sg_cors_allowed_origins: list[str] = ["http://localhost:8000", "http://main-service:8000"]
    sg_health_check_interval_seconds: float = 4.0
    sg_health_check_timeout_seconds: float = 1.0
    sg_http_client_timeout_seconds: float = 3.0
    sg_consistency_confidence_divergence_threshold: float = 0.3
    sg_consent_cache_ttl_seconds: int = 10  # 一期未使用，二期降级路径预留（对齐00b-统一配置参考手册）
    sg_latency_tracker_max_samples: int = 500
    # 节流判定的最小样本数（2026-09-29 真机死锁修复）：
    # 启动期样本只有 2~3 个（冷启动慢请求），p99≈max 必然超阈值 → 节流 →
    # 新请求被拒 → 永远没有新样本 → p99 冻结在超限值 → 自持锁死。
    # 样本数不足时 p99 不具统计意义，只报 unknown，绝不触发节流。
    # ⚠️ 2026-09-30 二次死锁（T2c 并发评测实测）：负载上升（并发 6）时真机
    # SG p99 天然 8.7→41.75ms，任何静态阈值都会再次自持锁死——基于延迟
    # 的节流在真机硬件档位不可用。**处置走部署配置**（真机 .env 设
    # SG_P99_MIN_SAMPLES_FOR_THROTTLE=10^9 语义停用节流，p99 仍监控上报），
    # 代码默认值保持开发语义 20；正式修复须改为错误率/饱和度熔断
    # （二期设计决策项，记 16 号 A.7）。测试不得读此默认值做循环量
    # （见 test_sg_p99_throttling.py helper 注释）。
    sg_p99_min_samples_for_throttle: int = 20
    sg_max_concurrent_checks: int = 100
    deployment_profile: str = "compact"
    log_level: str = "INFO"
    env: str = "development"

    @field_validator("safety_gateway_port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("must be between 1 and 65535")
        if v in _KNOWN_SERVICE_PORTS:
            raise ValueError(f"port {v} conflicts with another service. Reserved: {sorted(_KNOWN_SERVICE_PORTS)}")
        return v

    @field_validator("redis_pool_max_connections", "redis_pool_timeout")
    @classmethod
    def validate_positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be greater than 0")
        return v

    @field_validator(
        "sg_p99_latency_warning_threshold_ms",
        "sg_p99_latency_critical_threshold_ms",
        "sg_e2e_p99_latency_warning_threshold_ms",
        "sg_e2e_p99_latency_critical_threshold_ms",
        "sg_health_check_interval_seconds",
        "sg_health_check_timeout_seconds",
    )
    @classmethod
    def validate_positive_float(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be greater than 0")
        return v

    @model_validator(mode="after")
    def validate_cross_field_constraints(self) -> "SGSettings":
        if not self.database_url:
            import logging
            logging.getLogger(__name__).critical(
                "SG_DATABASE_URL is not set. SafetyGateway must connect to the same PostgreSQL "
                "database as the main service to query consent_records. "
                "Set DATABASE_URL in the SG environment to match the main service."
            )

        if self.sg_p99_latency_warning_threshold_ms >= self.sg_p99_latency_critical_threshold_ms:
            raise ValueError("sg_p99_latency_warning_threshold_ms must be less than sg_p99_latency_critical_threshold_ms")

        if self.sg_e2e_p99_latency_warning_threshold_ms >= self.sg_e2e_p99_latency_critical_threshold_ms:
            raise ValueError("sg_e2e_p99_latency_warning_threshold_ms must be less than sg_e2e_p99_latency_critical_threshold_ms")

        if self.safety_gateway_queue_timeout_seconds <= 5.0:
            raise ValueError("safety_gateway_queue_timeout_seconds must be greater than watchdog restart time (5s)")
        if self.safety_gateway_queue_timeout_seconds >= 15.0:
            raise ValueError("safety_gateway_queue_timeout_seconds must be less than sustained unavailability threshold (15s)")

        if self.sg_health_check_timeout_seconds >= self.sg_health_check_interval_seconds:
            raise ValueError("sg_health_check_timeout_seconds must be less than sg_health_check_interval_seconds")

        health_check_unavailable_seconds = 3 * (self.sg_health_check_interval_seconds + self.sg_health_check_timeout_seconds)
        if abs(health_check_unavailable_seconds - 15.0) > 0.1:
            raise ValueError(
                f"Health check unavailable threshold (3*(interval+timeout)={health_check_unavailable_seconds:.1f}s) "
                f"should be close to 15s per design spec (±0.1s tolerance)"
            )

        return self

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


sg_settings = SGSettings()
