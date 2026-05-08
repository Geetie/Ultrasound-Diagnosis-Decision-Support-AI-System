from typing import List
from pydantic import ValidationError
from app.config.settings import settings


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
    
    return errors


def validate_and_exit_on_error() -> None:
    errors = validate_config()
    if errors:
        raise ConfigValidationError(errors)