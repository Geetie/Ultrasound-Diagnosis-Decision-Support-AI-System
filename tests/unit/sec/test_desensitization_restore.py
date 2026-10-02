from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.track_sec, pytest.mark.track_data, pytest.mark.sec_domain_desens]

from app.security.desensitization_restore import (
    DesensitizationMappingRecord,
    DesensitizationRestoreService,
)


class TestDesensitizationRestoreService:
    def setup_method(self):
        self.service = DesensitizationRestoreService()

    @pytest.mark.asyncio
    async def test_encrypt_decrypt_roundtrip(self):
        plaintext = "张三"
        ciphertext, iv, key_version = await self.service._encrypt_value(plaintext)
        assert ciphertext != plaintext.encode()
        assert len(iv) == 12
        assert isinstance(key_version, int)
        decrypted = await self.service._decrypt_value(ciphertext, iv, key_version)
        assert decrypted == plaintext

    @pytest.mark.asyncio
    async def test_encrypt_different_iv_per_call(self):
        plaintext = "same_value"
        ct1, iv1, _ = await self.service._encrypt_value(plaintext)
        ct2, iv2, _ = await self.service._encrypt_value(plaintext)
        assert iv1 != iv2
        assert ct1 != ct2

    @pytest.mark.asyncio
    async def test_encrypt_decrypt_chinese_characters(self):
        plaintext = "身份证号310101199001011234"
        ciphertext, iv, kv = await self.service._encrypt_value(plaintext)
        decrypted = await self.service._decrypt_value(ciphertext, iv, kv)
        assert decrypted == plaintext

    @pytest.mark.asyncio
    async def test_encrypt_decrypt_empty_string(self):
        plaintext = ""
        ciphertext, iv, kv = await self.service._encrypt_value(plaintext)
        decrypted = await self.service._decrypt_value(ciphertext, iv, kv)
        assert decrypted == plaintext

    @pytest.mark.asyncio
    async def test_encrypt_decrypt_long_value(self):
        plaintext = "A" * 10000
        ciphertext, iv, kv = await self.service._encrypt_value(plaintext)
        decrypted = await self.service._decrypt_value(ciphertext, iv, kv)
        assert decrypted == plaintext

    @pytest.mark.asyncio
    async def test_rate_limit_allows_under_limit(self):
        result = await self.service._check_rate_limit()
        assert result is True

    @pytest.mark.asyncio
    async def test_rate_limit_blocks_over_limit(self):
        from app.config.settings import settings
        limit = settings.desensitization_restore_rate_limit
        now = datetime.now(timezone.utc)
        hour_key = now.strftime("%Y%m%d%H")
        self.service._rate_limit_counter = {hour_key: limit}
        self.service._rate_limit_window_start = now
        result = await self.service._check_rate_limit()
        assert result is False

    @pytest.mark.asyncio
    async def test_rate_limit_resets_after_window(self):
        self.service._rate_limit_counter = {"old_hour": 999}
        self.service._rate_limit_window_start = datetime.now(timezone.utc) - timedelta(hours=2)
        result = await self.service._check_rate_limit()
        assert result is True

    def test_get_restore_stats(self):
        stats = self.service.get_restore_stats()
        assert "total_restore_requests_current_window" in stats
        assert "current_key_version" in stats
        assert "key_cache_valid" in stats

    def test_key_cache_ttl(self):
        from datetime import timedelta

        from app.security.desensitization_restore import _KEY_CACHE_TTL
        assert timedelta(minutes=5) == _KEY_CACHE_TTL


class TestDesensitizationMappingRecord:
    def test_record_dataclass(self):
        record = DesensitizationMappingRecord(
            record_id="test-id",
            session_id="session-1",
            original_value="张三",
            desensitized_value="PATIENT_001",
            field_type="patient_name",
            key_version=1,
            created_at=datetime.now(timezone.utc),
        )
        assert record.record_id == "test-id"
        assert record.field_type == "patient_name"
        assert record.key_version == 1


class TestBatchRestoreLogging:
    def setup_method(self):
        self.service = DesensitizationRestoreService()

    @pytest.mark.asyncio
    async def test_empty_request_logs_debug(self):
        with patch("app.security.desensitization_restore.logger") as mock_logger:
            result = await self.service.batch_restore([])
            assert result == {}
            mock_logger.debug.assert_called_once_with("batch_restore_empty_request")

    @pytest.mark.asyncio
    async def test_rate_limited_logs_warning(self):
        with patch("app.security.desensitization_restore.logger") as mock_logger, patch.object(
            self.service, "_check_rate_limit_persistent", return_value=False
        ):
            with pytest.raises(RuntimeError):
                await self.service.batch_restore(["id1", "id2"])
            mock_logger.warning.assert_called_once_with(
                "batch_restore_rate_limited", record_count=2
            )

    @pytest.mark.asyncio
    async def test_successful_batch_restore_logs_started_and_completed(self):
        mapping_mock = MagicMock()
        mapping_mock.id = "id1"
        mapping_mock.encrypted_original = b"ciphertext"
        mapping_mock.iv = b"iv" * 6
        mapping_mock.key_version = 1

        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mapping_mock]
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        async def mock_get_session():
            yield mock_db_session

        with patch("app.security.desensitization_restore.logger") as mock_logger, \
             patch.object(
                 self.service, "_check_rate_limit_persistent", return_value=True
             ), \
             patch.object(
                 self.service, "_decrypt_value", return_value="decrypted_value"
             ), \
             patch("app.config.database.get_session", side_effect=mock_get_session):
            result = await self.service.batch_restore(["id1"])
            assert result == {"id1": "decrypted_value"}
            mock_logger.info.assert_any_call(
                "batch_restore_started", record_count=1, record_ids_sample=["id1"]
            )
            completed_calls = [
                c for c in mock_logger.info.call_args_list
                if c[0][0] == "batch_restore_completed"
            ]
            assert len(completed_calls) == 1
            completed_kwargs = completed_calls[0][1]
            assert completed_kwargs["total"] == 1
            assert completed_kwargs["success"] == 1
            assert completed_kwargs["not_found"] == 0
            assert completed_kwargs["failed"] == 0
            assert "elapsed_ms" in completed_kwargs

    @pytest.mark.asyncio
    async def test_batch_restore_logs_not_found_and_failed(self):
        mapping_mock = MagicMock()
        mapping_mock.id = "id1"
        mapping_mock.encrypted_original = b"ciphertext"
        mapping_mock.iv = b"iv" * 6
        mapping_mock.key_version = 1

        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mapping_mock]
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        async def mock_get_session():
            yield mock_db_session

        async def mock_decrypt(*args, **kwargs):
            if kwargs.get("key_version") == 1 or (len(args) >= 3 and args[2] == 1):
                raise RuntimeError("decrypt failed")
            return "value"

        with patch("app.security.desensitization_restore.logger") as mock_logger, \
             patch.object(
                 self.service, "_check_rate_limit_persistent", return_value=True
             ), \
             patch.object(
                 self.service, "_decrypt_value", side_effect=RuntimeError("decrypt failed")
             ), \
             patch("app.config.database.get_session", side_effect=mock_get_session):
            result = await self.service.batch_restore(["id1", "id2"])
            assert result == {"id1": None, "id2": None}
            completed_calls = [
                c for c in mock_logger.info.call_args_list
                if c[0][0] == "batch_restore_completed"
            ]
            assert len(completed_calls) == 1
            completed_kwargs = completed_calls[0][1]
            assert completed_kwargs["total"] == 2
            assert completed_kwargs["success"] == 0
            assert completed_kwargs["not_found"] == 1
            assert completed_kwargs["failed"] == 1
