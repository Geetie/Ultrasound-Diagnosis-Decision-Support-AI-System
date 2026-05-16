"""Safety Gateway错误类型定义模块

定义SG安全网关专用的细粒度异常类型，便于问题定位和错误处理。

错误类型层次结构：
- SafetyGatewayError (基类)
  - DatabaseError
    - DatabaseConnectionError
    - DatabaseQueryTimeoutError
    - DatabaseQueryError
  - ConsentError
    - ConsentCheckError
  - ValidationError
    - SensitiveDataError
    - ContraindicationError
    - ThresholdError
  - CircuitBreakerError
  - ConfigurationError
"""

from typing import Any, ClassVar


class SafetyGatewayError(Exception):

    code: str = "SG_ERROR"
    message: str = "安全网关错误"

    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None,
                 error_message: str | None = None, detail: str | None = None):
        effective_message = message or error_message or self.message
        super().__init__(effective_message)
        self.details = details or {}
        if detail:
            self.details["detail"] = detail

    _INTERNAL_TO_EXTERNAL_CODE: ClassVar[dict[str, str]] = {
        "SG_DB_ERROR": "E4005B",
        "SG_DB_CONNECTION": "E4005B",
        "SG_DB_TIMEOUT": "E4005B",
        "SG_DB_QUERY": "E4005B",
        "SG_CONSENT_ERROR": "E4005A2",
        "SG_CONSENT_CHECK": "E4005A2",
        "SG_CIRCUIT_BREAKER": "E4005A",
        "SG_CONFIG": "E4005",
        "SG_VALIDATION_ERROR": "E4005",
        "SG_SENSITIVE_DATA": "E4005A1",
        "SG_CONTRAINDICATION": "E3006",
        "SG_THRESHOLD": "E4005",
    }

    def to_dict(self) -> dict[str, Any]:
        external_code = self._INTERNAL_TO_EXTERNAL_CODE.get(self.code, self.code)
        return {
            "code": external_code,
            "internal_code": self.code if external_code != self.code else None,
            "message": str(self),
            "details": self.details,
        }


class DatabaseError(SafetyGatewayError):
    code = "SG_DB_ERROR"
    message = "数据库操作失败"


class DatabaseConnectionError(DatabaseError):
    code = "SG_DB_CONNECTION"
    message = "数据库连接失败"


class DatabaseQueryTimeoutError(DatabaseError):
    code = "SG_DB_TIMEOUT"
    message = "数据库查询超时"


class DatabaseQueryError(DatabaseError):
    code = "SG_DB_QUERY"
    message = "数据库查询执行失败"


class ConsentError(SafetyGatewayError):
    code = "SG_CONSENT_ERROR"
    message = "患者同意状态校验失败"


class ConsentCheckError(ConsentError):
    code = "SG_CONSENT_CHECK"
    message = "同意状态检查失败"


class ValidationError(SafetyGatewayError):
    code = "SG_VALIDATION_ERROR"
    message = "校验失败"


class SensitiveDataError(ValidationError):
    code = "SG_SENSITIVE_DATA"
    message = "检测到敏感数据"


class ContraindicationError(ValidationError):
    code = "SG_CONTRAINDICATION"
    message = "检测到禁忌条件"


class ThresholdError(ValidationError):
    code = "SG_THRESHOLD"
    message = "置信度低于阈值"


class CircuitBreakerError(SafetyGatewayError):
    code = "SG_CIRCUIT_BREAKER"
    message = "熔断器已打开"


class ConfigurationError(SafetyGatewayError):
    code = "SG_CONFIG"
    message = "配置错误"


class HealthCheckFailureError(SafetyGatewayError):
    code = "E4005A"
    message = "安全网关健康检查连续失败，服务不可用"


class ServiceUnavailableError(SafetyGatewayError):
    code = "E4005"
    message = "安全网关服务不可用"


class DesensitizationSafetyViolationError(SafetyGatewayError):
    code = "E4005A1"
    message = "脱敏防线降级且检测到敏感数据，拒绝输出"
    # SG扩展子码：01-系统架构设计§3.4仅定义E4005A/B/C，
    # E4005A1区分"脱敏防线降级+敏感数据"场景，需同步更新03-接口规范错误码表


class ConsentSafetyViolationError(SafetyGatewayError):
    code = "E4005A2"
    message = "安全校验不可用且患者同意状态异常（已撤回），拒绝输出"
    # SG扩展子码：区分"SG不可用+同意撤回"场景，需同步更新03-接口规范错误码表


class DatabaseUnavailableError(SafetyGatewayError):
    code = "E4005B"
    message = "数据库不可用，无法获取同意状态"


class CombinedUnavailableError(SafetyGatewayError):
    code = "E4005C"
    message = "SafetyGateway + 融合层同时不可用"
