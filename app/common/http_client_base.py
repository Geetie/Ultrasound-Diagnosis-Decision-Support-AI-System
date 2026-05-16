"""HTTP客户端公共基类模块。

本模块提供了基于HTTP协议的客户端抽象基类，封装了所有HTTP客户端的公共逻辑，
包括连接管理、请求构建、响应解析、错误处理、日志记录等。

核心功能：
- 统一的HTTP客户端管理（懒加载、自动重连、可配置连接池）
- 通用的HTTP请求方法（_do_get、_do_post_json、_do_post_multipart）
- 统一的错误处理和日志记录
- 健康检查接口
- 优雅关闭机制

设计模式：
- 模板方法模式：子类通过实现_create_*_error抽象方法来返回特定业务异常
- 策略模式：支持不同类型的HTTP服务客户端

错误处理：
- 子类通过实现_create_closed_error、_create_timeout_error等抽象方法
  来返回各自模块的业务异常类型
- 基类统一处理httpx异常并转换为业务异常
"""

import json
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
import structlog


class BaseHttpClient(ABC):
    """HTTP客户端公共基类。

    提供HTTP客户端的通用功能，包括连接管理、错误处理、日志记录等。
    子类需要实现异常工厂方法来返回特定的业务异常类型。

    Args:
        base_url: 服务的基础URL
        logger_name: 日志记录器名称
        timeout_seconds: 请求超时时间（默认30秒）
        connect_timeout_seconds: 连接超时时间（默认10秒）
        health_check_timeout_seconds: 健康检查超时时间（默认5秒）
        max_connections: 连接池最大连接数（默认10）
        max_keepalive_connections: 连接池最大保持连接数（默认5）
        keepalive_expiry: 连接池连接存活时间（默认300秒）

    Attributes:
        _base_url: 基础URL（已去除尾部斜杠）
        _client: httpx异步客户端（懒加载）
        _closed: 是否已关闭
        _log: 结构化日志记录器
    """

    def __init__(
        self,
        base_url: str,
        logger_name: str,
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 10.0,
        health_check_timeout_seconds: float = 5.0,
        max_connections: int = 10,
        max_keepalive_connections: int = 5,
        keepalive_expiry: float = 300.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._logger_name = logger_name
        self._timeout_seconds = timeout_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._health_check_timeout_seconds = health_check_timeout_seconds
        self._max_connections = max_connections
        self._max_keepalive_connections = max_keepalive_connections
        self._keepalive_expiry = keepalive_expiry
        self._client: httpx.AsyncClient | None = None
        self._closed = False
        self._log = structlog.get_logger(logger_name)

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建HTTP客户端。

        实现懒加载机制，首次调用时创建客户端，之后复用。
        如果客户端已关闭，抛出业务异常。

        Returns:
            httpx.AsyncClient: HTTP异步客户端

        Raises:
            Exception: 子类定义的业务异常
        """
        if self._closed:
            raise self._create_closed_error()

        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(
                max_connections=self._max_connections,
                max_keepalive_connections=self._max_keepalive_connections,
                keepalive_expiry=self._keepalive_expiry,
            )
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(
                    connect=self._connect_timeout_seconds,
                    read=self._timeout_seconds,
                    write=self._connect_timeout_seconds,
                    pool=self._connect_timeout_seconds,
                ),
                limits=limits,
            )
        return self._client

    @abstractmethod
    def _create_closed_error(self) -> Exception:
        """创建客户端已关闭的异常。

        子类必须实现此方法，返回特定的业务异常类型。

        Returns:
            Exception: 业务异常实例
        """
        ...

    @abstractmethod
    def _create_timeout_error(self, detail: str) -> Exception:
        """创建超时异常。

        子类必须实现此方法，返回特定的业务异常类型。

        Args:
            detail: 错误详情

        Returns:
            Exception: 业务异常实例
        """
        ...

    @abstractmethod
    def _create_connection_error(self, detail: str) -> Exception:
        """创建连接错误异常。

        子类必须实现此方法，返回特定的业务异常类型。

        Args:
            detail: 错误详情

        Returns:
            Exception: 业务异常实例
        """
        ...

    @abstractmethod
    def _create_http_error(
        self,
        status_code: int,
        detail: str,
        retryable: bool,
    ) -> Exception:
        """创建HTTP错误异常。

        子类必须实现此方法，返回特定的业务异常类型。

        Args:
            status_code: HTTP状态码
            detail: 错误详情
            retryable: 是否可重试

        Returns:
            Exception: 业务异常实例
        """
        ...

    @abstractmethod
    def _create_json_decode_error(self, detail: str) -> Exception:
        """创建JSON解析错误异常。

        子类必须实现此方法，返回特定的业务异常类型。

        Args:
            detail: 错误详情

        Returns:
            Exception: 业务异常实例
        """
        ...

    @abstractmethod
    def _create_unexpected_error(self, detail: str) -> Exception:
        """创建意外错误异常。

        子类必须实现此方法，返回特定的业务异常类型。

        Args:
            detail: 错误详情

        Returns:
            Exception: 业务异常实例
        """
        ...

    _max_error_body_length: int = 500

    async def _execute_request(
        self,
        method: str,
        endpoint: str,
        request_id: str | None = None,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        start_time = time.monotonic()
        client = await self._get_client()

        try:
            response = await client.request(method, endpoint, **kwargs)
        except httpx.TimeoutException as exc:
            latency_ms = (time.monotonic() - start_time) * 1000
            self._log.error(
                "timeout",
                engine=self._logger_name,
                endpoint=endpoint,
                request_id=request_id,
                session_id=session_id,
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )
            raise self._create_timeout_error(detail=str(exc)) from exc
        except httpx.ConnectError as exc:
            self._log.error(
                "connection_failed",
                engine=self._logger_name,
                endpoint=endpoint,
                base_url=self._base_url,
                request_id=request_id,
                session_id=session_id,
                error=str(exc),
            )
            raise self._create_connection_error(detail=str(exc)) from exc
        except Exception as exc:
            self._log.error(
                "unexpected_error",
                engine=self._logger_name,
                endpoint=endpoint,
                request_id=request_id,
                session_id=session_id,
                error=str(exc),
            )
            raise self._create_unexpected_error(detail=str(exc)) from exc

        if response.status_code != 200:
            error_body = response.text[:self._max_error_body_length]
            self._log.error(
                f"{method.lower()}_request_failed",
                engine=self._logger_name,
                endpoint=endpoint,
                status_code=response.status_code,
                error=error_body,
                request_id=request_id,
                session_id=session_id,
            )
            raise self._create_http_error(
                status_code=response.status_code,
                detail=error_body,
                retryable=response.status_code >= 500,
            )

        try:
            return response.json()
        except json.JSONDecodeError as exc:
            self._log.error(
                "json_decode_failed",
                engine=self._logger_name,
                endpoint=endpoint,
                request_id=request_id,
                session_id=session_id,
                error=str(exc),
            )
            raise self._create_json_decode_error(detail=str(exc)) from exc

    async def _do_get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._execute_request(
            "GET", endpoint,
            request_id=request_id, session_id=session_id,
            params=params,
        )

    async def _do_post_json(
        self,
        endpoint: str,
        payload: dict[str, Any],
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._execute_request(
            "POST", endpoint,
            request_id=request_id, session_id=session_id,
            json=payload,
        )

    async def _do_post_multipart(
        self,
        endpoint: str,
        files: list[tuple[str, tuple[str, bytes, str]]],
        data: dict[str, Any],
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._execute_request(
            "POST", endpoint,
            request_id=request_id, session_id=session_id,
            files=files, data=data,
        )

    async def health_check(self) -> bool:
        """检查服务健康状态。

        调用后端的/health端点进行健康检查。

        Returns:
            bool: True表示健康，False表示不健康
        """
        client = await self._get_client()
        try:
            response = await client.get(
                "/health",
                timeout=httpx.Timeout(
                    connect=self._connect_timeout_seconds,
                    read=self._health_check_timeout_seconds,
                    write=self._health_check_timeout_seconds,
                    pool=self._health_check_timeout_seconds,
                ),
            )
            return response.status_code == 200
        except (httpx.TimeoutException, httpx.ConnectError):
            self._log.warning(
                "health_check_failed",
                engine=self._logger_name,
                base_url=self._base_url,
            )
            return False

    async def close(self) -> None:
        """关闭HTTP客户端，释放资源。"""
        self._closed = True
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            self._log.info("client_closed", engine=self._logger_name)
