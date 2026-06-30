from typing import List
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    env: str = 'development'

    database_url: str = ''
    redis_url: str = 'redis://redis:6379/1'

    postgres_password: str = ''

    api_host: str = '0.0.0.0'
    api_port: int = 8000

    safety_gateway_host: str = 'localhost'
    safety_gateway_port: int = 9001
    safety_gateway_whitelist_fields: List[str] = Field(
        default=['organ_name', 'anatomical_location', 'echogenicity', 'boundary', 'morphology', 'vascularity', 'calcification', 'measurement'],
        alias='safety_gateway_whitelist_fields',
    )
    safety_gateway_enable_degraded_mode: bool = True
    safety_gateway_queue_timeout_seconds: float = 10.0
    safety_gateway_http_client_timeout_seconds: float = Field(default=3.0, alias='sg_http_client_timeout_seconds')
    safety_gateway_client_fast_fail_threshold: int = Field(default=3, alias='sg_client_fast_fail_threshold')
    safety_gateway_health_probe_interval_seconds: float = Field(default=4.0, alias='sg_health_probe_interval_seconds')
    desensitization_rules_safety_path: str = 'desensitization_rules_safety.yaml'
    desensitization_rules_safety_patch_path: str = 'desensitization_rules_safety_patch.yaml'
    db_query_timeout: int = 5
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_idle_in_transaction_timeout: int = 30

    log_db_pool_max_size: int = 3
    log_db_pool_min_size: int = 1

    safety_gateway_db_retry_max_attempts: int = Field(default=3, alias='sg_db_retry_max_attempts')
    safety_gateway_db_retry_delay_base: float = Field(default=0.5, alias='sg_db_retry_delay_base')
    safety_gateway_db_retry_max_total_time_seconds: float = Field(default=10.0, alias='sg_db_retry_max_total_time_seconds')
    safety_gateway_threshold_cache_ttl_seconds: int = Field(default=300, alias='sg_threshold_cache_ttl_seconds')
    safety_gateway_circuit_breaker_failure_threshold: int = Field(default=5, alias='sg_circuit_breaker_failure_threshold')
    safety_gateway_circuit_breaker_success_threshold: int = Field(default=3, alias='sg_circuit_breaker_success_threshold')
    safety_gateway_circuit_breaker_cooldown_period: float = Field(default=30.0, alias='sg_circuit_breaker_cooldown_period')

    safety_gateway_p99_latency_warning_threshold_ms: float = Field(default=4.0, alias='sg_p99_latency_warning_threshold_ms')
    safety_gateway_p99_latency_critical_threshold_ms: float = Field(default=5.0, alias='sg_p99_latency_critical_threshold_ms')

    redis_pool_max_connections: int = 50
    redis_pool_timeout: int = 5
    redis_ssl: bool = False
    redis_ssl_cert_reqs: str = 'required'
    redis_key_prefix: str = 'ultrasound'
    websocket_redis_pubsub_enabled: bool = True
    rate_limit_redis_enabled: bool = False

    jwt_algorithm: str = 'RS256'

    @field_validator("jwt_algorithm")
    @classmethod
    def jwt_algorithm_must_be_rs256(cls, v: str) -> str:
        if v != 'RS256':
            raise ValueError(
                f"jwt_algorithm must be RS256 (asymmetric), got '{v}'. "
                f"HS256 is prohibited per security compliance policy (04-安全合规.md §4)."
            )
        return v
    jwt_secret_key: str = ''
    jwt_expire_minutes: int = 480
    jwt_refresh_expire_days: int = 7

    log_level: str = 'INFO'

    allowed_origins: str = 'http://localhost:3000,http://localhost:8080,http://127.0.0.1:3000'

    max_concurrent_sessions_per_user: int = 5
    max_queued_requests_per_user: int = 2

    desensitization_restore_rate_limit: int = 100
    desensitization_restore_rate_limit_full: int = 200
    desensitization_key_rotation_days: int = 180
    desensitization_key_rotation_days_compact: int = 60
    desensitization_re_encrypt_batch_size: int = 1000

    hsm_emergency_signing_enabled: bool = False
    hsm_emergency_signing_max_daily: int = 50
    hsm_emergency_signing_verification_hours: int = 24
    hsm_degraded_phase_1_threshold_minutes: int = 5
    hsm_degraded_phase_2_threshold_minutes: int = 30
    jwt_key_rotation_days: int = 120
    jwt_key_rotation_days_compact: int = 60

    consent_withdrawal_consumer_enabled: bool = True
    consent_withdrawal_consumer_group: str = "consent_withdrawal_consumers"
    consent_withdrawal_consumer_name: str = "sec_consumer_01"
    consent_withdrawal_handlers: List[str] = Field(default_factory=lambda: ["safety_action", "data_cleanup"])
    consent_withdrawal_batch_size: int = 10
    consent_withdrawal_poll_timeout_ms: int = 5000

    deployment_profile: str = 'compact'

    inference_engine: str = 'llama.cpp'
    llama_cpp_host: str = 'localhost'
    llama_cpp_port: int = 8001
    llama_cpp_default_max_tokens: int = 1024
    vllm_host: str = 'localhost'
    vllm_port: int = 8002
    vllm_default_model: str = ''
    vllm_require_model: bool = True
    max_tokens: int = Field(default=2048, alias='inference_max_tokens')
    temperature: float = Field(default=0.1, alias='inference_temperature')
    top_p: float = Field(default=0.95, alias='inference_top_p')
    inference_timeout_seconds: float = 30.0
    inference_health_check_timeout_seconds: float = 5.0
    inference_connect_timeout_seconds: float = 10.0
    
    # 重试配置
    inference_max_retries: int = 3
    inference_retry_delay_base: float = 1.0
    inference_retry_delay_max: float = 10.0
    
    # 熔断配置
    inference_circuit_breaker_timeout: float = 30.0
    inference_circuit_breaker_failure_threshold: int = 5
    inference_circuit_breaker_success_threshold: int = 3
    
    # 连接池配置
    inference_pool_max_connections: int = 10
    inference_pool_keepalive_expiry: float = 300.0

    dicom_scp_port: int = 10400
    dicom_storage_backend: str = 'local'
    dicom_storage_path: str = './dicom_storage'
    dicom_storage_max_file_size: int = 524288000

    navigation_ws_heartbeat_interval_seconds: int = 5
    navigation_ws_heartbeat_timeout_seconds: int = 15
    navigation_ws_auth_token_ttl_seconds: int = 300
    navigation_confidence_threshold: float = 0.75
    nav_safety_check_normal_timeout_seconds: float = 0.05
    nav_safety_check_max_timeout_seconds: float = 0.08
    nav_verification_workers: int = 4
    nav_max_connections_per_session: int = 5
    session_max_ttl_seconds: int = 7200
    dicom_max_retransmit_per_frame: int = 3
    dicom_max_retransmit_frame_range: int = 90
    dicom_frame_buffer_capacity: int = 90

    phase0a_mock_processing_time_ms: int = 2500
    phase0a_mock_model_version: str = "thyroid_v1.0.0"
    phase0a_mock_kb_version: str = "kb_v1.0.0"

    # === T-GA-INT-01 端到端集成配置 ===
    int_worker_pool_size: int = 4
    int_pipeline_total_timeout_ms: int = 8000
    int_dlq_max_retries: int = 3
    int_worker_blpop_timeout_seconds: int = 5
    int_worker_idle_log_interval_seconds: int = 60
    int_pending_queue_max_length: int = 1000
    int_consumer_recovery_delay_seconds: float = 2.0

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "dicom-storage"
    minio_secure: bool = False

    embedding_model: str = 'ultrasound-clip'
    embedding_dim: int = 768
    embedding_dim_fallback: int = 1024
    ultrasound_clip_host: str = 'localhost'
    ultrasound_clip_port: int = 8003
    bge_m3_host: str = 'localhost'
    bge_m3_port: int = 8004
    embedding_timeout_seconds: float = 30.0
    embedding_health_check_timeout_seconds: float = 5.0
    embedding_connect_timeout_seconds: float = 10.0
    embedding_contradiction_threshold_ultrasound_clip: float = 0.85
    embedding_contradiction_threshold_bge_m3_medical: float = 0.85

    speech_engine: str = 'funasr'
    funasr_model_dir: str = 'models/funasr'
    funasr_device: str = 'cpu'
    funasr_num_threads: int = 4
    speech_timeout_seconds: float = 10.0

    vllm_gpu_memory_utilization: float = 0.90
    gpu_fragmentation_warning_threshold: float = 0.30
    gpu_fragmentation_critical_threshold: float = 0.40
    compact_memory_budget_mb: float = 22048.0

    obs_metrics_enabled: bool = True
    obs_metrics_port: int = 9090
    obs_metrics_scrape_interval: float = 15.0
    obs_victoriametrics_url: str = 'http://victoriametrics:8428'
    obs_tracing_enabled: bool = False
    obs_tracing_otlp_endpoint: str = 'http://jaeger:4317'
    obs_tracing_sample_rate: float = 0.1
    obs_audit_cross_chain_interval_hours: int = 1
    obs_audit_critical_operations: List[str] = Field(
        default_factory=lambda: [
            'threshold_adjustment', 'model_version_change',
            'kb_update', 'desensitization_rule_update',
        ]
    )
    obs_cache_monitor_enabled: bool = True
    obs_cusum_target_rate: float = 0.005
    obs_cusum_decision_threshold: float = 3.0
    obs_cusum_calibration_samples: int = 300
    obs_cusum_calibration_simulations: int = 1000
    obs_overall_hallucination_warning_rate: float = 0.03
    obs_overall_hallucination_critical_rate: float = 0.05

    alert_contacts_json: str = ''
    alert_escalation_warning_confirm_seconds: int = 14400
    alert_escalation_critical_confirm_seconds: int = 900
    alert_escalation_warning_escalate_seconds: int = 86400
    alert_escalation_critical_escalate_seconds: int = 3600
    alert_webhook_url: str = ''

    data_retention_outpatient_years: int = 15
    data_retention_inpatient_years: int = 30
    data_retention_audit_years: int = 30
    data_retention_operation_years: int = 5
    session_archive_after_days: int = 7
    data_retention_task_interval_hours: int = 24

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        populate_by_name=True,
        extra='ignore',
    )

    @field_validator("deployment_profile")
    @classmethod
    def deployment_profile_must_be_valid(cls, v: str) -> str:
        allowed = {'compact', 'full'}
        if v not in allowed:
            raise ValueError(f"deployment_profile must be one of {allowed}, got '{v}'")
        return v

    @model_validator(mode='after')
    def validate_deployment_config(self) -> 'Settings':
        import logging
        import os
        _log = logging.getLogger(__name__)

        if self.env == 'production':
            if os.environ.get('USE_REDIS_STORAGE', 'false').lower() != 'true':
                _log.warning(
                    "production模式强制使用Redis存储Refresh Token，已自动启用USE_REDIS_STORAGE=true"
                )
                os.environ['USE_REDIS_STORAGE'] = 'true'

            if not os.environ.get('HSM_PRIVATE_KEY_PASSWORD'):
                raise ValueError(
                    "HSM_PRIVATE_KEY_PASSWORD must be set in production environment"
                )

            if not os.environ.get('HSM_KEY_ENCRYPTION_KEY'):
                raise ValueError(
                    "HSM_KEY_ENCRYPTION_KEY must be set in production environment"
                )

            if not self.redis_url.startswith('rediss://'):
                _log.warning(
                    "production模式建议使用TLS加密Redis连接(rediss://)，当前使用: %s",
                    self.redis_url[:20] + "..."
                )

            if not os.environ.get('REDIS_PASSWORD'):
                raise ValueError(
                    "REDIS_PASSWORD must be set in production environment"
                )

        if self.deployment_profile == 'compact' and self.inference_engine != 'llama.cpp':
            _log.warning(
                "compact模式强制使用llama.cpp引擎，已从%s覆盖为llama.cpp",
                self.inference_engine,
            )
            self.inference_engine = 'llama.cpp'
        elif self.deployment_profile == 'full' and self.inference_engine != 'vllm':
            _log.warning(
                "full模式强制使用vllm引擎，已从%s覆盖为vllm",
                self.inference_engine,
            )
            self.inference_engine = 'vllm'

        if self.deployment_profile == 'compact':
            self.funasr_device = 'cpu'
            if self.desensitization_key_rotation_days > self.desensitization_key_rotation_days_compact:
                _log.info(
                    "compact模式使用缩短的密钥轮换周期，已从%d天覆盖为%d天",
                    self.desensitization_key_rotation_days,
                    self.desensitization_key_rotation_days_compact,
                )
                self.desensitization_key_rotation_days = self.desensitization_key_rotation_days_compact
            if self.jwt_key_rotation_days > self.jwt_key_rotation_days_compact:
                _log.info(
                    "compact模式使用缩短的JWT密钥轮换周期，已从%d天覆盖为%d天",
                    self.jwt_key_rotation_days,
                    self.jwt_key_rotation_days_compact,
                )
                self.jwt_key_rotation_days = self.jwt_key_rotation_days_compact
            if self.vllm_gpu_memory_utilization > 0.80:
                _log.warning(
                    "compact模式下vllm_gpu_memory_utilization超过0.80，已从%.2f覆盖为0.80",
                    self.vllm_gpu_memory_utilization,
                )
                self.vllm_gpu_memory_utilization = 0.80
            if self.vllm_gpu_memory_utilization < 0.50:
                raise ValueError(
                    f"vllm_gpu_memory_utilization must be >= 0.50 for compact mode, got {self.vllm_gpu_memory_utilization}"
                )
        else:
            if self.desensitization_restore_rate_limit < self.desensitization_restore_rate_limit_full:
                _log.info(
                    "full模式使用更高的脱敏还原速率限制，已从%d次/小时覆盖为%d次/小时",
                    self.desensitization_restore_rate_limit,
                    self.desensitization_restore_rate_limit_full,
                )
                self.desensitization_restore_rate_limit = self.desensitization_restore_rate_limit_full
            if self.vllm_gpu_memory_utilization > 0.95:
                raise ValueError(
                    f"vllm_gpu_memory_utilization must be <= 0.95 for full mode, got {self.vllm_gpu_memory_utilization}"
                )
            if self.vllm_gpu_memory_utilization < 0.70:
                raise ValueError(
                    f"vllm_gpu_memory_utilization must be >= 0.70 for full mode, got {self.vllm_gpu_memory_utilization}"
                )

        ports = [
            ('api_port', self.api_port),
            ('safety_gateway_port', self.safety_gateway_port),
            ('llama_cpp_port', self.llama_cpp_port),
            ('vllm_port', self.vllm_port),
            ('ultrasound_clip_port', self.ultrasound_clip_port),
            ('bge_m3_port', self.bge_m3_port),
            ('dicom_scp_port', self.dicom_scp_port),
        ]
        seen: dict[int, str] = {}
        for name, port in ports:
            if port in seen:
                raise ValueError(
                    f"Port conflict: {name}={port} conflicts with {seen[port]}={port}"
                )
            seen[port] = name

        return self

    @property
    def effective_max_tokens(self) -> int:
        """Compact模式下返回min(max_tokens, 1024)，full模式返回max_tokens。

        此属性为配置文档性质，供调用方参考compact模式下的推荐上限。
        实际运行时限制由引擎default_max_tokens控制（LlamaCpp=1024, VLLM=2048），
        InferenceRequest.max_tokens=None时引擎默认值生效。
        未来工厂模式可直接消费此属性创建引擎实例。
        """
        if self.deployment_profile == 'compact':
            return min(self.max_tokens, 1024)
        return self.max_tokens


settings = Settings()
