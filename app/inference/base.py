"""HTTP推理引擎基类模块。

本模块提供了基于HTTP协议的推理引擎抽象基类，封装了推理引擎的特定逻辑，
包括熔断机制、重试策略、模型信息缓存等。

通用HTTP客户端管理逻辑继承自BaseHttpClient。

核心功能：
- 熔断器模式（防止级联故障）
- 指数退避重试策略（带抖动）
- 模型信息缓存机制（TTL 300秒）

设计模式：
- 模板方法模式：子类通过实现_build_payload、_parse_response、_generate_endpoint
  三个抽象方法来实现特定后端的差异化逻辑
- 策略模式：支持llama.cpp和vLLM等不同后端

断路器状态：
- closed: 正常状态，允许请求通过
- open: 熔断状态，拒绝请求，直接返回错误
- half-open: 半开状态，允许少量请求探测服务是否恢复
"""

import asyncio
import json
import random
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

import httpx

from app.inference.constants import InferenceConstants as _IC
import structlog

from app.common.http_client_base import BaseHttpClient
from app.inference.protocols import (
    InferenceEngine,
    InferenceError,
    InferenceErrorCode,
    InferenceRequest,
    InferenceResponse,
    ModelInfo,
)

_MODEL_INFO_TTL_SECONDS = 300.0


class CircuitBreakerState(Enum):
    """断路器状态枚举。"""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class BaseHttpInferenceEngine(BaseHttpClient, InferenceEngine, ABC):
    """HTTP推理引擎基类。

    继承自BaseHttpClient，提供HTTP客户端的通用功能。
    实现了熔断机制和重试策略。

    Args:
        base_url: 推理服务的基础URL
        logger_name: 日志记录器名称
        timeout_seconds: 请求超时时间（默认30秒）
        connect_timeout_seconds: 连接超时时间（默认10秒）
        health_check_timeout_seconds: 健康检查超时时间（默认5秒）
        default_max_tokens: 默认最大生成Token数（默认2048）
        default_temperature: 默认采样温度（默认0.1）
        default_top_p: 默认Top-p采样参数（默认0.95）
        max_retries: 最大重试次数（默认3次）
        retry_delay_base: 重试延迟基数（默认1秒）
        retry_delay_max: 最大重试延迟（默认10秒）
        circuit_breaker_timeout: 断路器超时时间（默认30秒）
        circuit_breaker_failure_threshold: 断路器失败阈值（默认5次）
        circuit_breaker_success_threshold: 断路器成功阈值（默认3次）
        pool_max_connections: 连接池最大连接数（默认10）
        pool_keepalive_expiry: 连接池连接存活时间（默认300秒）
    """

    def __init__(
        self,
        base_url: str,
        logger_name: str,
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 10.0,
        health_check_timeout_seconds: float = 5.0,
        default_max_tokens: int = 2048,
        default_temperature: float = 0.1,
        default_top_p: float = 0.95,
        max_retries: int = 3,
        retry_delay_base: float = 1.0,
        retry_delay_max: float = 10.0,
        circuit_breaker_timeout: float = 30.0,
        circuit_breaker_failure_threshold: int = 5,
        circuit_breaker_success_threshold: int = 3,
        pool_max_connections: int = 10,
        pool_keepalive_expiry: float = 300.0,
    ):
        super().__init__(
            base_url=base_url,
            logger_name=logger_name,
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            health_check_timeout_seconds=health_check_timeout_seconds,
            max_connections=pool_max_connections,
            max_keepalive_connections=max(pool_max_connections // 2, 1),
            keepalive_expiry=pool_keepalive_expiry,
        )
        self._default_max_tokens = default_max_tokens
        self._default_temperature = default_temperature
        self._default_top_p = default_top_p

        self._max_retries = max_retries
        self._retry_delay_base = retry_delay_base
        self._retry_delay_max = retry_delay_max

        self._circuit_breaker_timeout = circuit_breaker_timeout
        self._circuit_breaker_failure_threshold = circuit_breaker_failure_threshold
        self._circuit_breaker_success_threshold = circuit_breaker_success_threshold
        self._circuit_breaker_state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0

        self._model_info_cache: ModelInfo | None = None
        self._model_info_cached_at: float = 0.0

    def _create_closed_error(self) -> Exception:
        return InferenceError(
            error_code=InferenceErrorCode.GENERATION_FAILED,
            message="engine has been closed",
            detail=f"engine={self._logger_name}",
            retryable=False,
        )

    def _create_timeout_error(self, detail: str) -> Exception:
        return InferenceError(
            error_code=InferenceErrorCode.TIMEOUT,
            message="inference request timed out",
            detail=detail,
            retryable=True,
            retry_strategy=_IC.RETRY_STRATEGY_WAIT_5S,
        )

    def _create_connection_error(self, detail: str) -> Exception:
        return InferenceError(
            error_code=InferenceErrorCode.CONNECTION_FAILED,
            message="cannot connect to inference server",
            detail=detail,
            retryable=True,
            retry_strategy=_IC.RETRY_STRATEGY_WAIT_5S,
        )

    def _create_http_error(
        self,
        status_code: int,
        detail: str,
        retryable: bool,
    ) -> Exception:
        return InferenceError(
            error_code=InferenceErrorCode.GENERATION_FAILED,
            message=f"returned HTTP {status_code}",
            detail=detail,
            retryable=retryable,
            retry_strategy=_IC.RETRY_STRATEGY_WAIT_5S if retryable else _IC.RETRY_STRATEGY_NO_RETRY,
        )

    def _create_json_decode_error(self, detail: str) -> Exception:
        return InferenceError(
            error_code=InferenceErrorCode.GENERATION_FAILED,
            message="returned invalid JSON",
            detail=detail,
            retryable=True,
            retry_strategy=_IC.RETRY_STRATEGY_WAIT_5S,
        )

    def _create_unexpected_error(self, detail: str) -> Exception:
        return InferenceError(
            error_code=InferenceErrorCode.GENERATION_FAILED,
            message="unexpected error during inference",
            detail=detail,
            retryable=False,
        )

    def _update_circuit_breaker_state(self, success: bool) -> None:
        """更新断路器状态。

        Args:
            success: 请求是否成功
        """
        now = time.monotonic()

        if success:
            if self._circuit_breaker_state == CircuitBreakerState.OPEN:
                self._circuit_breaker_state = CircuitBreakerState.HALF_OPEN
                self._success_count = 1
                self._failure_count = 0
            elif self._circuit_breaker_state == CircuitBreakerState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._circuit_breaker_success_threshold:
                    self._circuit_breaker_state = CircuitBreakerState.CLOSED
                    self._success_count = 0
                    self._failure_count = 0
                    self._log.info(
                        "circuit_breaker_closed",
                        engine=self._logger_name,
                    )
            else:
                self._failure_count = 0
        else:
            if self._circuit_breaker_state == CircuitBreakerState.HALF_OPEN:
                self._circuit_breaker_state = CircuitBreakerState.OPEN
                self._last_failure_time = now
                self._failure_count = 0
                self._success_count = 0
                self._log.warning(
                    "circuit_breaker_open",
                    engine=self._logger_name,
                )
            elif self._circuit_breaker_state == CircuitBreakerState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self._circuit_breaker_failure_threshold:
                    self._circuit_breaker_state = CircuitBreakerState.OPEN
                    self._last_failure_time = now
                    self._failure_count = 0
                    self._success_count = 0
                    self._log.warning(
                        "circuit_breaker_open",
                        engine=self._logger_name,
                        failures=self._circuit_breaker_failure_threshold,
                    )

    def _is_circuit_breaker_open(self) -> bool:
        """检查断路器是否处于打开状态。

        Returns:
            bool: 如果断路器打开返回True，否则返回False
        """
        if self._circuit_breaker_state == CircuitBreakerState.CLOSED:
            return False

        if self._circuit_breaker_state == CircuitBreakerState.HALF_OPEN:
            return False

        now = time.monotonic()
        if (now - self._last_failure_time) >= self._circuit_breaker_timeout:
            self._circuit_breaker_state = CircuitBreakerState.HALF_OPEN
            self._success_count = 0
            self._failure_count = 0
            self._log.info(
                "circuit_breaker_half_open",
                engine=self._logger_name,
            )
            return False

        return True

    async def _wait_retry(self, attempt: int) -> None:
        """指数退避等待。

        Args:
            attempt: 当前重试次数（从0开始）
        """
        delay = self._retry_delay_base * (2 ** attempt)
        delay = min(delay, self._retry_delay_max)
        jitter = delay * 0.25
        delay = delay + random.uniform(-jitter, jitter)
        await asyncio.sleep(max(0, delay))

    @abstractmethod
    def _build_payload(self, request: InferenceRequest) -> dict[str, Any]:
        """构建请求体。

        子类必须实现此方法，将InferenceRequest转换为特定后端的请求格式。

        Args:
            request: 推理请求对象

        Returns:
            dict: 请求体字典
        """
        raise NotImplementedError

    @abstractmethod
    def _parse_response(self, data: dict[str, Any], request: InferenceRequest) -> InferenceResponse:
        """解析响应。

        子类必须实现此方法，将后端响应转换为统一的InferenceResponse格式。

        Args:
            data: 后端返回的JSON数据
            request: 原始请求对象

        Returns:
            InferenceResponse: 统一的响应对象
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def _generate_endpoint(self) -> str:
        """获取生成端点路径。

        子类必须实现此属性，返回特定后端的生成端点路径。

        Returns:
            str: 端点路径（如"/v1/completions"）
        """
        raise NotImplementedError

    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        self._check_circuit_breaker(request)

        start_time = time.monotonic()
        payload = self._build_payload(request)
        last_error: InferenceError | None = None

        for attempt in range(self._max_retries + 1):
            try:
                result = await self._execute_single_request(payload, request, start_time, attempt)
                return result
            except InferenceError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self._max_retries:
                    raise
                self._update_circuit_breaker_state(False)
                await self._wait_retry(attempt)
            except httpx.TimeoutException as exc:
                last_error = self._handle_timeout(exc, request, start_time, attempt)
                if last_error is not None:
                    raise last_error
                self._update_circuit_breaker_state(False)
                await self._wait_retry(attempt)
            except httpx.ConnectError as exc:
                last_error = self._handle_connect_error(exc, request, attempt)
                if last_error is not None:
                    raise last_error
                self._update_circuit_breaker_state(False)
                await self._wait_retry(attempt)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                last_error = self._handle_unexpected_error(exc, request, attempt)
                if last_error is not None:
                    raise last_error
                self._update_circuit_breaker_state(False)
                await self._wait_retry(attempt)

        if last_error:
            raise last_error

        raise InferenceError(
            error_code=InferenceErrorCode.GENERATION_FAILED,
            message="max retries exceeded",
            detail=f"Failed after {self._max_retries + 1} attempts",
            retryable=False,
        )

    def _check_circuit_breaker(self, request: InferenceRequest) -> None:
        if self._is_circuit_breaker_open():
            self._log.error(
                "circuit_breaker_open_reject",
                engine=self._logger_name,
                request_id=request.request_id,
            )
            raise InferenceError(
                error_code=InferenceErrorCode.CONNECTION_FAILED,
                message="circuit breaker is open, service unavailable",
                detail="Circuit breaker tripped, please try again later",
                retryable=True,
                retry_strategy="retry_after_30s",
            )

    async def _execute_single_request(
        self,
        payload: dict,
        request: InferenceRequest,
        start_time: float,
        attempt: int,
    ) -> InferenceResponse:
        client = await self._get_client()
        response = await client.post(self._generate_endpoint, json=payload)
        latency_ms = (time.monotonic() - start_time) * 1000

        if response.status_code != 200:
            self._handle_http_error(response, request, attempt)

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            self._log.error(
                "json_decode_failed",
                engine=self._logger_name,
                request_id=request.request_id,
                error=str(exc),
                attempt=attempt,
            )

            self._update_circuit_breaker_state(False)
            raise InferenceError(
                error_code=InferenceErrorCode.GENERATION_FAILED,
                message="returned invalid JSON",
                detail=str(exc),
                retryable=True,
                retry_strategy=_IC.RETRY_STRATEGY_WAIT_5S,
            ) from exc

        result = self._parse_response(data, request)
        result.latency_ms = round(latency_ms, 2)
        result.retry_count = attempt

        self._update_circuit_breaker_state(True)

        self._log.info(
            "generation_completed",
            engine=self._logger_name,
            request_id=request.request_id,
            session_id=request.session_id,
            latency_ms=result.latency_ms,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            finish_reason=result.finish_reason,
            attempt=attempt,
        )

        return result

    def _handle_http_error(self, response, request: InferenceRequest, attempt: int) -> None:
        error_body = response.text[:500]
        self._log.error(
            "generation_failed",
            engine=self._logger_name,
            status_code=response.status_code,
            error=error_body,
            request_id=request.request_id,
            attempt=attempt,
        )

        self._update_circuit_breaker_state(False)
        raise InferenceError(
            error_code=InferenceErrorCode.GENERATION_FAILED,
            message=f"returned HTTP {response.status_code}",
            detail=error_body,
            retryable=response.status_code >= 500,
            retry_strategy=_IC.RETRY_STRATEGY_WAIT_5S if response.status_code >= 500 else _IC.RETRY_STRATEGY_NO_RETRY,
        )

    def _handle_timeout(
        self, exc: httpx.TimeoutException, request: InferenceRequest,
        start_time: float, attempt: int,
    ) -> InferenceError | None:
        latency_ms = (time.monotonic() - start_time) * 1000
        self._log.error(
            "timeout",
            engine=self._logger_name,
            request_id=request.request_id,
            latency_ms=round(latency_ms, 2),
            error=str(exc),
            attempt=attempt,
        )

        if attempt >= self._max_retries:
            self._update_circuit_breaker_state(False)
            return InferenceError(
                error_code=InferenceErrorCode.TIMEOUT,
                message="inference request timed out",
                detail=str(exc),
                retryable=True,
                retry_strategy=_IC.RETRY_STRATEGY_WAIT_5S,
            )
        return None

    def _handle_connect_error(
        self, exc: httpx.ConnectError, request: InferenceRequest, attempt: int,
    ) -> InferenceError | None:
        self._log.error(
            "connection_failed",
            engine=self._logger_name,
            base_url=self._base_url,
            error=str(exc),
            attempt=attempt,
        )

        if attempt >= self._max_retries:
            self._update_circuit_breaker_state(False)
            return InferenceError(
                error_code=InferenceErrorCode.CONNECTION_FAILED,
                message="cannot connect to inference server",
                detail=f"Connection to {self._base_url} failed: {exc}",
                retryable=True,
                retry_strategy=_IC.RETRY_STRATEGY_WAIT_5S,
            )
        return None

    def _handle_unexpected_error(
        self, exc: Exception, request: InferenceRequest, attempt: int,
    ) -> InferenceError | None:
        self._log.error(
            "unexpected_error",
            engine=self._logger_name,
            request_id=request.request_id,
            error=str(exc),
            attempt=attempt,
        )

        if attempt >= self._max_retries:
            self._update_circuit_breaker_state(False)
            return InferenceError(
                error_code=InferenceErrorCode.GENERATION_FAILED,
                message="unexpected error during inference",
                detail=str(exc),
                retryable=False,
            )
        return None

    async def get_model_info(self) -> ModelInfo:
        """获取模型信息。

        从后端获取当前加载的模型信息，使用TTL缓存（300秒）避免频繁查询。

        Returns:
            ModelInfo: 模型信息对象，如果获取失败返回空对象
        """
        now = time.monotonic()
        if self._model_info_cache is not None and (now - self._model_info_cached_at) < _MODEL_INFO_TTL_SECONDS:
            return self._model_info_cache

        client = await self._get_client()
        try:
            response = await client.get("/v1/models")
            if response.status_code != 200:
                self._log.warning(
                    "model_info_unavailable",
                    engine=self._logger_name,
                    status_code=response.status_code,
                )
                return ModelInfo()

            try:
                data = response.json()
            except json.JSONDecodeError as exc:
                self._log.warning(
                    "model_info_json_decode_failed",
                    engine=self._logger_name,
                    error=str(exc),
                )
                return ModelInfo()

            models = data.get("data", [])
            if not models:
                return ModelInfo()

            model_data = models[0]
            self._model_info_cache = ModelInfo(
                model_name=model_data.get("id", ""),
                model_version="",
                quantization="",
                context_length=model_data.get("context_length", 0) or 0,
                vocab_size=model_data.get("vocab_size", 0) or 0,
            )
            self._model_info_cached_at = time.monotonic()
            return self._model_info_cache

        except InferenceError:
            raise
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            self._log.warning("model_info_fetch_failed", engine=self._logger_name, error=str(exc))
            return ModelInfo()
