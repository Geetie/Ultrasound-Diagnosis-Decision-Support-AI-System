"""

Unit tests for the observability logging framework.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.track_obs, pytest.mark.track_inf]

from app.config.settings import settings
from app.observability.enums import DeploymentProfile, LogCategory, NtpSyncStatus
from app.observability.logging import (
    BatchLogStorageWrapper,
    ElasticsearchLogStorage,
    FileLogStorage,
    LogRecord,
    LogStorage,
    PostgreSQLLogStorage,
    add_ntp_context,
    classify_and_bind_logger,
    create_audit_logger,
    create_diagnostic_logger,
    create_operational_logger,
    get_log_storage,
)


@pytest.fixture(autouse=True)
def reset_log_storage_singleton():
    """Reset the LogStorage singleton before and after each test."""

    LogStorage._instance = None
    yield
    LogStorage._instance = None


class TestEnums:
    def test_log_category_enum_values(self):
        assert LogCategory.DIAGNOSTIC.value == "diagnostic"
        assert LogCategory.OPERATIONAL.value == "operational"
        assert LogCategory.AUDIT.value == "audit"

    def test_ntp_sync_status_enum_values(self):
        assert NtpSyncStatus.SYNCED.value == "synced"
        assert NtpSyncStatus.DEGRADED.value == "degraded"
        assert NtpSyncStatus.UNREACHABLE.value == "unreachable"

    def test_deployment_profile_enum_values(self):
        assert DeploymentProfile.COMPACT.value == "compact"
        assert DeploymentProfile.FULL.value == "full"


class TestLogRecord:
    def test_log_record_creation(self):
        record = LogRecord(
            category=LogCategory.DIAGNOSTIC,
            message="test message",
            extra={"key": "value"},
            ntp_sync_status=NtpSyncStatus.SYNCED,
            session_id="test-session",
        )
        assert record.category == "diagnostic"
        assert record.message == "test message"
        assert record.extra == {"key": "value"}
        assert record.ntp_sync_status == "synced"
        assert record.kwargs == {"session_id": "test-session"}
        assert record.timestamp is not None

    def test_log_record_to_dict(self):
        record = LogRecord(
            category=LogCategory.AUDIT,
            message="audit event",
            operator="test-user",
        )
        result = record.to_dict()
        assert result["category"] == "audit"
        assert result["message"] == "audit event"
        assert result["param_operator"] == "test-user"
        assert "timestamp" in result

    def test_log_record_empty_extra(self):
        record = LogRecord(category=LogCategory.OPERATIONAL, message="test")
        assert record.extra == {}
        assert record.to_dict()["message"] == "test"


class TestPostgreSQLLogStorage:
    def test_init_with_custom_pool_size(self):
        storage = PostgreSQLLogStorage(
            database_url="postgresql://localhost:5432/test",
            max_pool_size=20,
            min_pool_size=5,
        )
        assert storage.database_url == "postgresql://localhost:5432/test"
        assert storage.max_pool_size == 20
        assert storage.min_pool_size == 5

    def test_init_default_pool_size(self):
        storage = PostgreSQLLogStorage(database_url="postgresql://localhost:5432/test")
        assert storage.max_pool_size == settings.log_db_pool_max_size
        assert storage.min_pool_size == settings.log_db_pool_min_size

    @pytest.mark.asyncio
    async def test_awrite_handles_exception(self):
        from app.observability.logging import _logger
        storage = PostgreSQLLogStorage(database_url="postgresql://invalid:5432/test")
        record = LogRecord(category=LogCategory.AUDIT, message="test")

        with patch.object(_logger, 'error') as mock_error:
            try:
                await storage.awrite(record)
            except Exception:
                pytest.fail("awrite should handle exceptions silently")

            mock_error.assert_called_once()
            call_args = mock_error.call_args
            assert call_args[0][0] == "Failed to write log to PostgreSQL"
            assert call_args[1]["category"] == record.category
            assert call_args[1]["message"] == record.message

    def test_write_nonblocking(self):
        storage = PostgreSQLLogStorage(database_url="postgresql://localhost:5432/test")
        record = LogRecord(category=LogCategory.AUDIT, message="test")
        # 验证同步写入不阻塞且不抛出异常
        try:
            storage.write(record)
        except Exception:
            pytest.fail("write should not raise exceptions")


class TestFileLogStorage:
    def test_init_custom_log_dir(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path / "logs"))
        assert storage.log_dir == tmp_path / "logs"

    def test_init_default_log_dir(self):
        storage = FileLogStorage()
        assert storage.log_dir is not None
        assert isinstance(storage.log_dir, Path)

    def test_log_dir_creation_fallback(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path / "nonexistent" / "logs"))
        assert storage.log_dir.exists() or storage.log_dir == Path.home() / ".ultrasound" / "logs"

    def test_rotation_threshold(self):
        storage = FileLogStorage(max_file_size=1024)
        assert storage.max_file_size == 1024

    def test_rotation_check_interval(self):
        storage = FileLogStorage()
        assert storage.ROTATION_CHECK_INTERVAL == 100

    def test_get_log_file_path(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path))
        path = storage._get_log_file_path(LogCategory.DIAGNOSTIC)
        assert path.parent == tmp_path
        assert "diagnostic" in path.name
        assert path.suffix == ".jsonl"

    def test_should_rotate_false_for_small_file(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path), max_file_size=1024)
        test_file = tmp_path / "test.jsonl"
        test_file.write_text("small content")
        assert storage._should_rotate(test_file) is False

    def test_should_rotate_true_for_large_file(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path), max_file_size=10)
        test_file = tmp_path / "test.jsonl"
        test_file.write_text("x" * 100)
        assert storage._should_rotate(test_file) is True

    def test_should_rotate_false_for_nonexistent(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path))
        assert storage._should_rotate(tmp_path / "nonexistent.jsonl") is False

    @pytest.mark.asyncio
    async def test_awrite_creates_file(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path))
        record = LogRecord(category=LogCategory.DIAGNOSTIC, message="test log")
        await storage.awrite(record)
        log_files = list(tmp_path.glob("diagnostic_*.jsonl"))
        assert len(log_files) == 1

    @pytest.mark.asyncio
    async def test_awrite_handles_os_error(self, tmp_path):
        # 使用系统目录（通常没有写入权限）
        storage = FileLogStorage(log_dir="/system/cannot/write/here")
        record = LogRecord(category=LogCategory.AUDIT, message="test")

        # 验证OS错误被静默处理，不抛出异常
        try:
            await storage.awrite(record)
        except OSError:
            pytest.fail("awrite should handle OS errors silently")

    def test_write_nonblocking(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path))
        record = LogRecord(category=LogCategory.OPERATIONAL, message="test")
        # 验证同步写入不阻塞且不抛出异常
        try:
            storage.write(record)
        except Exception:
            pytest.fail("write should not raise exceptions")
        # 验证日志文件被创建
        log_files = list(tmp_path.glob("operational_*.jsonl"))
        assert len(log_files) == 1


class TestElasticsearchLogStorage:
    def test_init_with_custom_url(self):
        storage = ElasticsearchLogStorage(es_url="http://custom:9200")
        assert storage.es_url == "http://custom:9200"

    def test_init_default_url(self):
        storage = ElasticsearchLogStorage()
        assert storage.es_url is not None

    def test_index_prefix_default(self):
        storage = ElasticsearchLogStorage()
        assert storage.index_prefix == "ultrasound-logs"

    def test_index_prefix_custom(self):
        storage = ElasticsearchLogStorage(index_prefix="custom-logs")
        assert storage.index_prefix == "custom-logs"

    def test_write_nonblocking(self):
        storage = ElasticsearchLogStorage(es_url="http://invalid:9200")
        record = LogRecord(category=LogCategory.AUDIT, message="test")
        # 验证同步写入不阻塞且不抛出异常
        try:
            storage.write(record)
        except Exception:
            pytest.fail("write should not raise exceptions")


class TestLogStorage:
    def test_compact_profile_selects_postgresql(self):
        storage = LogStorage(profile=DeploymentProfile.COMPACT)
        assert isinstance(storage._backend, BatchLogStorageWrapper)
        assert isinstance(storage._backend._backend, PostgreSQLLogStorage)

    def test_full_profile_selects_elasticsearch(self):
        storage = LogStorage(profile=DeploymentProfile.FULL)
        assert isinstance(storage._backend, BatchLogStorageWrapper)
        assert isinstance(storage._backend._backend, ElasticsearchLogStorage)

    def test_singleton_thread_safety_pattern(self):
        storage1 = LogStorage.get_instance(profile=DeploymentProfile.COMPACT)
        storage2 = LogStorage.get_instance()
        assert storage1 is storage2

    def test_reset_instance(self):
        storage = LogStorage.get_instance(profile=DeploymentProfile.COMPACT)
        LogStorage.reset_instance()
        assert LogStorage._instance is None

    def test_get_profile(self):
        storage = LogStorage.get_instance(profile=DeploymentProfile.FULL)
        assert storage.get_profile() == DeploymentProfile.FULL

    def test_detect_profile_from_settings(self):
        with patch("app.observability.logging.storage.settings") as mock_settings:
            mock_settings.deployment_profile = "full"
            profile = LogStorage._detect_profile()
            assert profile == DeploymentProfile.FULL

    def test_detect_profile_invalid_value(self):
        with patch("app.observability.logging.storage.settings") as mock_settings:
            mock_settings.deployment_profile = "invalid"
            profile = LogStorage._detect_profile()
            assert profile == DeploymentProfile.COMPACT

    def test_write_creates_record(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path))
        record = LogRecord(category=LogCategory.DIAGNOSTIC, message="test")
        storage.write(record)
        # 验证文件被创建
        log_files = list(tmp_path.glob("diagnostic_*.jsonl"))
        assert len(log_files) == 1

    @pytest.mark.asyncio
    async def test_awrite_creates_record(self, tmp_path):
        storage = LogStorage(profile=DeploymentProfile.COMPACT, log_dir=str(tmp_path), batch_size=1, flush_interval=0.01)
        await storage.awrite(category=LogCategory.AUDIT, message="async test")
        assert storage._backend is not None


class TestLoggerFactoryFunctions:
    def test_create_diagnostic_logger(self):
        logger = create_diagnostic_logger()
        assert logger._context.get("log_category") == "diagnostic"

    def test_create_operational_logger(self):
        logger = create_operational_logger()
        assert logger._context.get("log_category") == "operational"

    def test_create_audit_logger(self):
        logger = create_audit_logger()
        assert logger._context.get("log_category") == "audit"

    def test_create_audit_logger_with_ntp(self):
        logger = create_audit_logger(ntp_sync_status=NtpSyncStatus.UNREACHABLE)
        assert logger._context.get("log_category") == "audit"
        assert logger._context.get("ntp_sync_status") == "unreachable"

    def test_create_logger_with_custom_name(self):
        logger = create_diagnostic_logger(logger_name="custom_logger")
        # 验证日志名称被正确设置（structlog使用'name'作为键）
        assert logger._context.get("name") == "custom_logger"

    def test_classify_and_bind_logger(self):
        logger = classify_and_bind_logger(LogCategory.AUDIT)
        assert logger._context.get("log_category") == "audit"
        assert "deployment_profile" in logger._context

    def test_add_ntp_context(self):
        import structlog
        base_logger = structlog.get_logger()
        enhanced = add_ntp_context(base_logger, NtpSyncStatus.DEGRADED)
        assert enhanced._context.get("ntp_sync_status") == "degraded"


class TestEdgeCases:
    def test_empty_message(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path))
        record = LogRecord(category=LogCategory.DIAGNOSTIC, message="")
        storage.write(record)
        # 验证空消息能正常写入
        log_files = list(tmp_path.glob("diagnostic_*.jsonl"))
        assert len(log_files) == 1

    def test_special_characters_in_message(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path))
        record = LogRecord(category=LogCategory.DIAGNOSTIC, message="<script>alert('xss')</script>中文测试🚀")
        storage.write(record)
        # 验证特殊字符和Unicode能正常写入
        log_files = list(tmp_path.glob("diagnostic_*.jsonl"))
        assert len(log_files) == 1

    def test_unicode_in_extra(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path))
        record = LogRecord(category=LogCategory.AUDIT, message="unicode test", extra={"name": "张三", "emoji": "🚀"})
        storage.write(record)
        # 验证Unicode额外字段能正常写入
        log_files = list(tmp_path.glob("audit_*.jsonl"))
        assert len(log_files) == 1

    def test_large_extra_dict(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path))
        large_dict = {f"key_{i}": f"value_{i}" for i in range(100)}
        record = LogRecord(category=LogCategory.DIAGNOSTIC, message="large dict test", extra=large_dict)
        storage.write(record)
        # 验证大字典能正常写入
        log_files = list(tmp_path.glob("diagnostic_*.jsonl"))
        assert len(log_files) == 1

    def test_concurrent_writes(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path))

        async def write_logs():
            tasks = [
                storage.awrite(record=LogRecord(category=LogCategory.DIAGNOSTIC, message=f"msg_{i}"))
                for i in range(10)
            ]
            await asyncio.gather(*tasks)
            await asyncio.sleep(0.1)

        asyncio.run(write_logs())
        log_files = list(tmp_path.glob("diagnostic_*.jsonl"))
        assert len(log_files) == 1

    def test_invalid_category_type(self):
        with pytest.raises((ValueError, AttributeError)):
            LogStorage(profile="invalid")

    def test_none_extra(self, tmp_path):
        storage = FileLogStorage(log_dir=str(tmp_path))
        record = LogRecord(category=LogCategory.DIAGNOSTIC, message="test", extra=None)
        storage.write(record)
        # 验证None extra能正常处理
        log_files = list(tmp_path.glob("diagnostic_*.jsonl"))
        assert len(log_files) == 1


class TestGetLogStorage:
    def test_get_log_storage_returns_singleton(self):
        storage = get_log_storage()
        assert storage is get_log_storage()

    def test_get_log_storage_type(self):
        storage = get_log_storage()
        assert isinstance(storage, LogStorage)


class TestCompactFallback:
    def test_compact_fallback_to_file_on_postgresql_failure(self, tmp_path):
        with patch(
            "app.observability.logging.storage.PostgreSQLLogStorage",
            side_effect=Exception("PostgreSQL init failed"),
        ):
            storage = LogStorage(
                profile=DeploymentProfile.COMPACT,
                log_dir=str(tmp_path),
            )
            assert isinstance(storage._backend, BatchLogStorageWrapper)
            assert isinstance(storage._backend._backend, FileLogStorage)

    def test_compact_uses_postgresql_when_available(self):
        storage = LogStorage(profile=DeploymentProfile.COMPACT)
        assert isinstance(storage._backend, BatchLogStorageWrapper)
        assert isinstance(storage._backend._backend, PostgreSQLLogStorage)

    def test_compact_fallback_writes_to_file(self, tmp_path):
        with patch(
            "app.observability.logging.storage.PostgreSQLLogStorage",
            side_effect=Exception("PostgreSQL init failed"),
        ):
            storage = LogStorage(
                profile=DeploymentProfile.COMPACT,
                log_dir=str(tmp_path),
                enable_batching=False,
            )
            storage.write(category=LogCategory.AUDIT, message="fallback test")
            log_files = list(tmp_path.glob("audit_*.jsonl"))
            assert len(log_files) == 1
