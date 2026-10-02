import json
from http import HTTPStatus
from typing import Any

import structlog
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.models.schemas import ErrorResponse
from app.observability.request_id import get_current_request_id

logger = structlog.get_logger()


def _coerce_detail(detail: Any) -> str | None:
    """把 ``HTTPException.detail`` 归一为字符串（``ErrorResponse`` 的字段都是 str）。

    ## 为什么必须归一（2026-09-28 真机部署暴露）

    FastAPI 允许 ``detail`` 是任意 JSON 可序列化对象，项目里**确实**这么用：
    ``app/api/v1/health.py`` 在 unhealthy 时把整份健康报告 dict 作为 detail 抛出。

    而原实现是 ``error_message=exc.detail or "Request error"`` 直接塞进 ``str``
    字段，于是**异常处理器自己抛 pydantic ValidationError**：

        pydantic_core.ValidationError: 1 validation error for ErrorResponse
        error_message
          Input should be a valid string [input_value={'status': 'unhealthy', ...}]

    处理器一崩，响应根本没有机会发出；异常再穿过 ``BaseHTTPMiddleware``
    （rate_limit）的 task group 抛出 ExceptionGroup —— 客户端看到的是**连接被重置**
    （curl ``http=000``），而不是 503 + 健康报告。真机表现极具误导性：
    "端口在 LISTEN，但任何非 2xx 请求都像被黑洞吞掉"。
    """
    if detail is None:
        return None
    if isinstance(detail, str):
        return detail
    try:
        return json.dumps(detail, ensure_ascii=False, default=str)
    except Exception:
        return str(detail)


def _default_message(status_code: int) -> str:
    """按状态码给出可读的兜底描述（结构化 detail 不适合放进 error_message）。"""
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Request error"


_ERROR_CODE_REGISTRY: dict[str, str] = {}


class ErrorSpec:
    __slots__ = ("code", "message", "retry_strategy", "retryable", "status_code", "suggestion")

    def __init__(self, code: str, message: str, status_code: int,
                 suggestion: str | None = None, retryable: bool = False,
                 retry_strategy: str = "no_retry"):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.suggestion = suggestion
        self.retryable = retryable
        self.retry_strategy = retry_strategy


_SPEC_REGISTRY: dict[str, ErrorSpec] = {}


class AppBaseException(HTTPException):
    _error_code: str = ""
    _error_message: str = ""
    _status_code: int = 400
    _suggestion: str | None = None
    _retryable: bool = False
    _retry_strategy: str = "no_retry"

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls._error_code and cls._error_message:
            _ERROR_CODE_REGISTRY[cls._error_code] = cls._error_message
            _SPEC_REGISTRY[cls._error_code] = ErrorSpec(
                code=cls._error_code,
                message=cls._error_message,
                status_code=cls._status_code,
                suggestion=cls._suggestion,
                retryable=cls._retryable,
                retry_strategy=cls._retry_strategy,
            )

    def __init__(
        self,
        error_code: str,
        error_message: str,
        status_code: int = 400,
        detail: str | None = None,
        request_id: str | None = None,
        suggestion: str | None = None,
        retryable: bool = False,
        retry_strategy: str = "no_retry",
        headers: dict[str, Any] | None = None,
    ):
        self.error_code = error_code
        self.error_message = error_message
        self.detail = detail
        self.request_id = request_id
        self.suggestion = suggestion
        self.retryable = retryable
        self.retry_strategy = retry_strategy
        super().__init__(status_code=status_code, detail=error_message, headers=headers)


class BadRequestException(AppBaseException):
    _error_code = "E1001"
    _error_message = "Bad request"
    _status_code = 400

    def __init__(self, error_code: str | None = None, error_message: str | None = None, **kwargs):
        super().__init__(
            error_code=error_code or self._error_code,
            error_message=error_message or self._error_message,
            status_code=400, **kwargs)


class NotFoundException(AppBaseException):
    _error_code = "E1002"
    _error_message = "Resource not found"
    _status_code = 404

    def __init__(self, error_code: str | None = None, error_message: str | None = None, **kwargs):
        super().__init__(error_code=error_code or self._error_code, error_message=error_message or self._error_message, status_code=404, **kwargs)


class PatientNotConsentedException(AppBaseException):
    _error_code = "E1008"
    _error_message = "Patient has not consented"
    _status_code = 403
    _suggestion = "Please obtain patient informed consent first"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=403,
            suggestion="请先获取患者知情同意",
            **kwargs
        )


class PatientConsentWithdrawnException(AppBaseException):
    _error_code = "E1010"
    _error_message = "Patient consent withdrawn"
    _status_code = 403
    _suggestion = "Please re-obtain patient informed consent"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=403,
            suggestion="请重新获取患者知情同意",
            **kwargs
        )


class RateLimitExceededException(AppBaseException):
    _error_code = "E1007"
    _error_message = "Rate limit exceeded"
    _status_code = 429
    _suggestion = "Please retry later"
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        kwargs.setdefault("headers", {"Retry-After": "5"})
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=429,
            suggestion="请稍后重试",
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )


class MissingDICOMTagException(AppBaseException):
    _error_code = "E1012"
    _error_message = "Missing required DICOM tags"
    _status_code = 400
    _suggestion = "Please upload a DICOM file with all required tags"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=400,
            suggestion="请上传包含所有必需标签的DICOM文件",
            **kwargs
        )


class AdversarialInputException(AppBaseException):
    _error_code = "E1006"
    _error_message = "Adversarial input detected"
    _status_code = 400

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=400,
            **kwargs
        )


class RetryLimitExceededException(AppBaseException):
    _error_code = "E1009"
    _error_message = "Retry limit exceeded"
    _status_code = 429
    _retryable = True
    _retry_strategy = "retry_after_30s"

    def __init__(self, error_message: str = None, **kwargs):
        kwargs.setdefault("headers", {"Retry-After": "30"})
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=429,
            retryable=True,
            retry_strategy="retry_after_30s",
            **kwargs
        )


class UnsupportedExamTypeException(AppBaseException):
    _error_code = "E1011"
    _error_message = "Unsupported exam type"
    _status_code = 400

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=400,
            **kwargs
        )


class InvalidIdempotencyKeyException(AppBaseException):
    _error_code = "E1013"
    _error_message = "Invalid idempotency key"
    _status_code = 400

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=400,
            **kwargs
        )


class ServerErrorException(AppBaseException):
    _error_code = "E2000"
    _error_message = "Internal server error"
    _status_code = 500

    def __init__(self, error_code: str | None = None, error_message: str | None = None, status_code: int = 500, **kwargs):
        super().__init__(
            error_code=error_code or self._error_code,
            error_message=error_message or self._error_message,
            status_code=status_code, **kwargs)


class KnowledgeRetrievalTimeoutException(AppBaseException):
    _error_code = "E2001"
    _error_message = "Knowledge retrieval timeout"
    _status_code = 200
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=200,
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )


class ModelInferenceFailedException(AppBaseException):
    _error_code = "E2002"
    _error_message = "Model inference failed"
    _status_code = 500
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=500,
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )


class DiagnosisQueueFullException(AppBaseException):
    _error_code = "E2004"
    _error_message = "Diagnosis queue full"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )


class FusionLayerUnavailableException(AppBaseException):
    _error_code = "E2003"
    _error_message = "Fusion layer unavailable"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )


class SystemQueueFullException(AppBaseException):
    _error_code = "E2004A"
    _error_message = "System queue full"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_30s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_30s",
            **kwargs
        )


class UserQueueQuotaExceededException(AppBaseException):
    _error_code = "E2004B"
    _error_message = "User queue quota exceeded"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_30s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_30s",
            **kwargs
        )


class RetrievalNoResultException(AppBaseException):
    _error_code = "E2006"
    _error_message = "Retrieval no result or insufficient evidence"
    _status_code = 200
    _suggestion = "Please adjust retrieval parameters or perform manual review"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=200,
            suggestion="请调整检索参数或进行人工审核",
            **kwargs
        )


class ModelHotReloadException(AppBaseException):
    _error_code = "E2007"
    _error_message = "Model hot reload in progress"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_30s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_30s",
            **kwargs
        )


class SafetyException(AppBaseException):
    def __init__(self, error_code: str, error_message: str, **kwargs):
        super().__init__(error_code=error_code, error_message=error_message, status_code=200, **kwargs)


class ConfidenceBelowThresholdException(SafetyException):
    _error_code = "E3001"
    _error_message = "Confidence below threshold"
    _status_code = 200
    _suggestion = "Manual review required"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            suggestion="需人工审核",
            **kwargs
        )


class TripleValidationFailedException(SafetyException):
    _error_code = "E3002"
    _error_message = "Triple validation failed"
    _status_code = 200
    _suggestion = "Manual review required"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            suggestion="需人工审核",
            **kwargs
        )


class SelfRAGQualityCheckFailedException(SafetyException):
    _error_code = "E3003"
    _error_message = "Self-RAG quality check failed"
    _status_code = 200
    _suggestion = "Manual review or retry with fallback"
    _retryable = True
    _retry_strategy = "retry_with_fallback"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            suggestion="需人工审核或使用降级方案重试",
            retryable=True,
            retry_strategy="retry_with_fallback",
            **kwargs
        )


class SpecialityConclusionContradictException(SafetyException):
    _error_code = "E3004"
    _error_message = "Specialty conclusion contradicts evidence"
    _status_code = 200
    _suggestion = "Manual review required"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            suggestion="需人工审核",
            **kwargs
        )


class SpecialityConclusionPartialException(SafetyException):
    _error_code = "E3005"
    _error_message = "Specialty conclusion partially consistent"
    _status_code = 200
    _suggestion = "Manual review required"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            suggestion="需人工审核",
            **kwargs
        )


class SafetyOutputBlockedException(SafetyException):
    _error_code = "E3006"
    _error_message = "Safety check failed, output blocked"
    _status_code = 200
    _suggestion = "Manual review required"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            suggestion="需人工审核",
            **kwargs
        )


class DICOMFormatException(AppBaseException):
    _error_code = "E1004"
    _error_message = "DICOM file format error"
    _status_code = 400
    _suggestion = "Please upload a valid DICOM file"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=400,
            suggestion="请上传有效的DICOM文件",
            **kwargs
        )


class InfrastructureException(AppBaseException):
    def __init__(self, error_code: str, error_message: str, status_code: int = 500, **kwargs):
        super().__init__(error_code=error_code, error_message=error_message, status_code=status_code, **kwargs)


class SafetyGatewayUnavailableException(InfrastructureException):
    _error_code = "E4005"
    _error_message = "Safety gateway unavailable"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )


class DeviceIsolatedException(InfrastructureException):
    _error_code = "E4010"
    _error_message = "Device isolated"
    _status_code = 403

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=403,
            **kwargs
        )


class KnowledgeVersionIncompatibleException(InfrastructureException):
    _error_code = "E4004"
    _error_message = "Knowledge base version incompatible"
    _status_code = 503

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            **kwargs
        )


class KnowledgeBaseSafetyUpdateException(InfrastructureException):
    _error_code = "E4006"
    _error_message = "Knowledge base safety update, in-progress diagnoses cancelled"
    _status_code = 503
    _suggestion = "Please create a new session with the latest knowledge base"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            suggestion="请使用最新知识库创建新会话",
            **kwargs
        )


class SafetyGatewayDownException(InfrastructureException):
    _error_code = "E4005A"
    _error_message = "Safety gateway down"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )


class PostgreSQLUnavailableException(InfrastructureException):
    _error_code = "E4005B"
    _error_message = "PostgreSQL unavailable"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )


class DualServiceUnavailableException(InfrastructureException):
    _error_code = "E4005C"
    _error_message = "Safety gateway and fusion layer both unavailable"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_30s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_30s",
            **kwargs
        )


class HSMUnavailableException(InfrastructureException):
    _error_code = "E4013"
    _error_message = "HSM unavailable"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )


class RedisUnavailableNewSessionDelayException(InfrastructureException):
    _error_code = "E4008"
    _error_message = "Redis unavailable causing new session creation delay (PostgreSQL fallback)"
    _status_code = 503
    _suggestion = "New sessions will fall back to PostgreSQL"
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_5s",
            suggestion="新会话将降级写入PostgreSQL",
            **kwargs
        )


class EdgeSchedulerUnreachableException(InfrastructureException):
    _error_code = "E4001"
    _error_message = "Edge scheduler unreachable"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )


class EdgeNavModelLoadFailedException(InfrastructureException):
    _error_code = "E4002"
    _error_message = "Edge navigation model load failed"
    _status_code = 503

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            **kwargs
        )


class DesensitizationCheckTimeoutException(InfrastructureException):
    _error_code = "E4003"
    _error_message = "Desensitization check timeout"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )


class RedisUnavailableException(InfrastructureException):
    _error_code = "E4007"
    _error_message = "Redis unavailable"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )


class PostgreSQLWriteTimeoutException(InfrastructureException):
    _error_code = "E4009"
    _error_message = "PostgreSQL write timeout"
    _status_code = 503
    _retryable = True
    _retry_strategy = "retry_after_5s"

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            retryable=True,
            retry_strategy="retry_after_5s",
            **kwargs
        )


class GPUOverheatException(InfrastructureException):
    _error_code = "E4011"
    _error_message = "GPU overheat protection"
    _status_code = 503

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            **kwargs
        )


class RetrievalServiceDegradedException(InfrastructureException):
    _error_code = "E4012"
    _error_message = "Retrieval service degraded for over 4 hours"
    _status_code = 503

    def __init__(self, error_message: str = None, **kwargs):
        super().__init__(
            error_code=self._error_code,
            error_message=error_message or self._error_message,
            status_code=503,
            **kwargs
        )


async def app_exception_handler(request: Request, exc: AppBaseException):
    error_response = ErrorResponse(
        error_code=exc.error_code,
        error_message=exc.error_message,
        detail=exc.detail,
        # exc 自带的 request_id 优先（调用方显式指定时语义更准）；缺省时
        # 回退到 RequestIdMiddleware 注入的请求级 id（P0-1.5 接线）。
        request_id=exc.request_id or get_current_request_id(),
        suggestion=exc.suggestion,
        retryable=exc.retryable,
        retry_strategy=exc.retry_strategy,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response.model_dump(),
        headers=exc.headers,
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    status_to_error_code = {
        400: "E1001",
        401: "E1003",
        403: "E1008",
        404: "E1002",
        405: "E1005",
        422: "E1006",
        500: "E2000",
        502: "E2000",
        503: "E4005",
    }
    error_code = status_to_error_code.get(exc.status_code, "E2000")
    # `detail` 可能是 dict（健康检查就拿它当结构化载荷用）。必须先归一为字符串，
    # 否则 pydantic 拒收 → 处理器自身崩溃 → 响应根本发不出去（详见 _coerce_detail）。
    detail_text = _coerce_detail(exc.detail)
    error_response = ErrorResponse(
        error_code=error_code,
        error_message=(
            detail_text if isinstance(exc.detail, str) else _default_message(exc.status_code)
        ) or "Request error",
        detail=detail_text,
        request_id=get_current_request_id(),
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response.model_dump(),
        headers=exc.headers,
    )


async def general_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", error=str(exc), path=request.url.path)
    error_response = ErrorResponse(
        error_code="E2000",
        error_message="Internal server error",
        detail=None,
        suggestion="Please contact administrator",
        request_id=get_current_request_id(),
    )
    return JSONResponse(
        status_code=500,
        content=error_response.model_dump(),
    )


ERROR_CODE_MAP = _SPEC_REGISTRY


def define_simple_error(
    name: str,
    error_code: str,
    error_message: str,
    status_code: int = 400,
    suggestion: str | None = None,
    retryable: bool = False,
    retry_strategy: str = "no_retry",
    base_class=AppBaseException,
):
    cls = type(name, (base_class,), {
        "_error_code": error_code,
        "_error_message": error_message,
        "_status_code": status_code,
        "_suggestion": suggestion,
        "_retryable": retryable,
        "_retry_strategy": retry_strategy,
        "__module__": __name__,
    })

    def __init__(self, error_message: str = None, **kwargs):
        init_kwargs = {
            "error_code": self._error_code,
            "error_message": error_message or self._error_message,
            "status_code": self._status_code,
        }
        if self._suggestion:
            init_kwargs["suggestion"] = self._suggestion
        if self._retryable:
            init_kwargs["retryable"] = self._retryable
        if self._retry_strategy != "no_retry":
            init_kwargs["retry_strategy"] = self._retry_strategy
        init_kwargs.update(kwargs)
        base_class.__init__(self, **init_kwargs)

    cls.__init__ = __init__
    return cls


def get_error_message(error_code: str) -> str:
    spec = _SPEC_REGISTRY.get(error_code)
    return spec.message if spec else "Unknown error"


def register_error(code: str, message: str, status_code: int, **kwargs) -> ErrorSpec:
    spec = ErrorSpec(code=code, message=message, status_code=status_code, **kwargs)
    _SPEC_REGISTRY[code] = spec
    return spec


def raise_error(code: str, message_override: str | None = None, **kwargs):
    spec = _SPEC_REGISTRY.get(code)
    if not spec:
        raise ServerErrorException(error_code="E2999", error_message=f"Unknown error code: {code}")
    raise AppBaseException(
        error_code=code,
        error_message=message_override or spec.message,
        status_code=spec.status_code,
        suggestion=kwargs.pop("suggestion", spec.suggestion),
        retryable=kwargs.pop("retryable", spec.retryable),
        retry_strategy=kwargs.pop("retry_strategy", spec.retry_strategy),
        **kwargs,
    )


register_error("E1003", "Authentication failed", 401)
register_error("E1005", "Method not allowed", 405)
register_error("E2000", "Internal server error", 500)
