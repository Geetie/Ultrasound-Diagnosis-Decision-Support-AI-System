import asyncio
import threading
from enum import Enum
from typing import Any, Optional

import structlog

from app.config.settings import settings
from app.observability.enums import (
    DeploymentProfile,
    LogCategory,
    NtpSyncStatus,
)
from app.observability.tracing import inject_trace_to_structlog

from .backend import LogStorageBackend
from .batch import BatchLogStorageWrapper
from .elasticsearch import ElasticsearchLogStorage
from .file import FileLogStorage
from .postgresql import PostgreSQLLogStorage
from .record import LogRecord


def _json_serializer(obj: Any) -> str:
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


structlog.configure(
    processors=[
        # OBS: structlog contextvars（request_id 中间件 bind 的 request_id）
        # 与 OTel trace context（trace_id/span_id）注入每条日志 ——
        # 修复 05-可观测性 附录A §7 "inject_trace_to_structlog 未加入 processors，
        # 即使打开开关日志也不会有 trace_id" 的断链。
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        inject_trace_to_structlog,
        structlog.processors.JSONRenderer(serializer=_json_serializer)
        if settings.env == "production"
        else structlog.dev.ConsoleRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)


class LogStorage:
    _instance: Optional["LogStorage"] = None
    _lock = threading.Lock()
    _backend: LogStorageBackend | None = None
    _profile: DeploymentProfile = DeploymentProfile.COMPACT

    def __init__(
        self,
        profile: DeploymentProfile = DeploymentProfile.COMPACT,
        database_url: str | None = None,
        log_dir: str | None = None,
        es_url: str | None = None,
        enable_batching: bool = True,
        batch_size: int = BatchLogStorageWrapper.DEFAULT_BATCH_SIZE,
        flush_interval: float = BatchLogStorageWrapper.DEFAULT_FLUSH_INTERVAL,
    ):
        if not isinstance(profile, DeploymentProfile):
            raise ValueError(f"Invalid profile type: {type(profile).__name__}. Must be DeploymentProfile enum.")

        self._profile = profile
        self._log_dir = log_dir
        self._es_url = es_url
        self._enable_batching = enable_batching

        raw_backend: LogStorageBackend
        if profile == DeploymentProfile.COMPACT:
            raw_backend = self._create_compact_backend(database_url, log_dir)
        elif profile == DeploymentProfile.FULL:
            raw_backend = ElasticsearchLogStorage(
                es_url=es_url,
            )
        else:
            raw_backend = PostgreSQLLogStorage(
                database_url=database_url or settings.database_url,
            )

        if enable_batching:
            self._backend = BatchLogStorageWrapper(
                backend=raw_backend,
                batch_size=batch_size,
                flush_interval=flush_interval,
            )
        else:
            self._backend = raw_backend

    @staticmethod
    def _create_compact_backend(
        database_url: str | None = None,
        log_dir: str | None = None,
    ) -> LogStorageBackend:
        try:
            backend = PostgreSQLLogStorage(
                database_url=database_url or settings.database_url,
            )
            return backend
        except Exception:
            _logger = structlog.get_logger("app.observability.logging.storage")
            _logger.warning(
                "postgresql_init_failed_fallback_to_file",
                log_dir=log_dir,
            )
            return FileLogStorage(log_dir=log_dir)

    @classmethod
    def get_instance(
        cls,
        profile: DeploymentProfile | None = None,
        database_url: str | None = None,
        log_dir: str | None = None,
        es_url: str | None = None,
        enable_batching: bool = True,
        batch_size: int = BatchLogStorageWrapper.DEFAULT_BATCH_SIZE,
        flush_interval: float = BatchLogStorageWrapper.DEFAULT_FLUSH_INTERVAL,
    ) -> "LogStorage":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    profile = profile or cls._detect_profile()
                    cls._instance = cls(
                        profile=profile,
                        database_url=database_url,
                        log_dir=log_dir,
                        es_url=es_url,
                        enable_batching=enable_batching,
                        batch_size=batch_size,
                        flush_interval=flush_interval,
                    )
        return cls._instance

    @classmethod
    def _detect_profile(cls) -> DeploymentProfile:
        env = getattr(settings, "deployment_profile", None)
        if env:
            try:
                return DeploymentProfile(env)
            except ValueError:  # silent-ok: 部署 profile 名无法识别时回退 COMPACT（默认档）
                pass
        return DeploymentProfile.COMPACT

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            if cls._instance and cls._instance._backend:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(cls._instance._backend.close())
                except RuntimeError:  # silent-ok: 无事件循环时同步关闭后端
                    asyncio.run(cls._instance._backend.close())
            cls._instance = None

    def get_profile(self) -> DeploymentProfile:
        return self._profile

    def get_metrics(self) -> dict[str, Any]:
        if isinstance(self._backend, BatchLogStorageWrapper):
            return self._backend.get_metrics()
        return {}

    def write(
        self,
        category: LogCategory,
        message: str,
        extra: dict[str, Any] | None = None,
        ntp_sync_status: NtpSyncStatus | None = None,
        **kwargs,
    ) -> None:
        if self._backend:
            record = LogRecord(
                category=category,
                message=message,
                extra=extra,
                ntp_sync_status=ntp_sync_status,
                **kwargs,
            )
            self._backend.write(record)

    async def awrite(
        self,
        category: LogCategory,
        message: str,
        extra: dict[str, Any] | None = None,
        ntp_sync_status: NtpSyncStatus | None = None,
        **kwargs,
    ) -> None:
        if self._backend:
            record = LogRecord(
                category=category,
                message=message,
                extra=extra,
                ntp_sync_status=ntp_sync_status,
                **kwargs,
            )
            await self._backend.awrite(record)


def get_log_storage() -> LogStorage:
    return LogStorage.get_instance()


def add_ntp_context(
    logger: structlog.stdlib.BoundLogger,
    ntp_sync_status: NtpSyncStatus,
) -> structlog.stdlib.BoundLogger:
    return logger.bind(ntp_sync_status=ntp_sync_status.value)


def classify_and_bind_logger(
    category: LogCategory,
    logger_name: str | None = None,
    ntp_sync_status: NtpSyncStatus | None = None,
) -> structlog.stdlib.BoundLogger:
    logger = structlog.get_logger(name=logger_name)

    logger = logger.bind(
        log_category=category.value,
        deployment_profile=get_log_storage().get_profile().value,
    )

    if ntp_sync_status:
        logger = logger.bind(ntp_sync_status=ntp_sync_status.value)

    return logger


def create_diagnostic_logger(
    logger_name: str | None = None,
    ntp_sync_status: NtpSyncStatus | None = None,
) -> structlog.stdlib.BoundLogger:
    return classify_and_bind_logger(
        category=LogCategory.DIAGNOSTIC,
        logger_name=logger_name,
        ntp_sync_status=ntp_sync_status,
    )


def create_operational_logger(
    logger_name: str | None = None,
    ntp_sync_status: NtpSyncStatus | None = None,
) -> structlog.stdlib.BoundLogger:
    return classify_and_bind_logger(
        category=LogCategory.OPERATIONAL,
        logger_name=logger_name,
        ntp_sync_status=ntp_sync_status,
    )


def create_audit_logger(
    logger_name: str | None = None,
    ntp_sync_status: NtpSyncStatus | None = None,
) -> structlog.stdlib.BoundLogger:
    return classify_and_bind_logger(
        category=LogCategory.AUDIT,
        logger_name=logger_name,
        ntp_sync_status=ntp_sync_status,
    )
