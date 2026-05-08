from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    env: str = 'development'

    database_url: str = 'postgresql+asyncpg://admin:supersecret@postgres:5432/ultrasound_db'
    redis_url: str = 'redis://redis:6379/0'

    postgres_password: str = 'default-password-must-be-changed-in-production-env'

    api_host: str = '0.0.0.0'
    api_port: int = 8000

    safety_gateway_host: str = '0.0.0.0'
    safety_gateway_port: int = 9001

    redis_pool_max_connections: int = 50
    redis_pool_timeout: int = 5

    jwt_algorithm: str = 'RS256'
    jwt_secret_key: str = ''
    jwt_expire_minutes: int = 480
    jwt_refresh_expire_days: int = 7

    log_level: str = 'INFO'

    allowed_origins: str = 'http://localhost:3000,http://localhost:8080,http://127.0.0.1:3000'

    max_concurrent_sessions_per_user: int = 5
    max_queued_requests_per_user: int = 2

    desensitization_restore_rate_limit: int = 100

    deployment_profile: str = 'compact'

    inference_engine: str = 'llama.cpp'
    llama_cpp_host: str = 'localhost'
    llama_cpp_port: int = 8001
    vllm_host: str = 'localhost'
    vllm_port: int = 8002
    max_tokens: int = Field(default=2048, alias='inference_max_tokens')
    temperature: float = Field(default=0.1, alias='inference_temperature')
    top_p: float = Field(default=0.95, alias='inference_top_p')
    inference_timeout_seconds: float = 30.0
    inference_health_check_timeout_seconds: float = 5.0
    inference_connect_timeout_seconds: float = 10.0

    dicom_scp_port: int = 10400

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

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        populate_by_name=True,
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
        if self.deployment_profile == 'compact' and self.inference_engine != 'llama.cpp':
            self.inference_engine = 'llama.cpp'
        elif self.deployment_profile == 'full' and self.inference_engine != 'vllm':
            self.inference_engine = 'vllm'

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
