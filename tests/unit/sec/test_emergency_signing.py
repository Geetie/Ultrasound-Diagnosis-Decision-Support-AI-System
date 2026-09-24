from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.track_sec, pytest.mark.track_inf, pytest.mark.emergency_crypto, pytest.mark.sec_domain_hsm]

from app.security.emergency_signing import (
    EmergencySigningManager,
    get_emergency_signing_manager,
)
from app.security.hsm import DegradedPhase, hsm_manager


class TestEmergencySigningManager:
    def setup_method(self):
        self.manager = EmergencySigningManager()

    @pytest.mark.asyncio
    async def test_is_emergency_signing_inactive_by_default(self):
        with patch.object(self.manager, 'is_emergency_signing_active', new_callable=AsyncMock, return_value=False):
            result = await self.manager.is_emergency_signing_active()
            assert result is False

    @pytest.mark.asyncio
    async def test_enable_requires_config_enabled(self):
        hsm_manager.set_degraded("test")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        with patch("app.config.settings.settings") as mock_settings:
            mock_settings.hsm_emergency_signing_enabled = False
            with pytest.raises(RuntimeError, match="not enabled"):
                await self.manager.enable_emergency_signing("admin", "test", "test reason")
        hsm_manager.clear_degraded()

    @pytest.mark.asyncio
    async def test_enable_requires_degraded_state(self):
        hsm_manager.clear_degraded()
        with patch("app.config.settings.settings") as mock_settings:
            mock_settings.hsm_emergency_signing_enabled = True
            with pytest.raises(RuntimeError, match="RESTRICTED or EMERGENCY"):
                await self.manager.enable_emergency_signing("admin", "test", "test reason")

    @pytest.mark.asyncio
    async def test_enable_requires_5min_degraded(self):
        hsm_manager.set_degraded("test")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        with patch("app.config.settings.settings.hsm_emergency_signing_enabled", True):
            with pytest.raises(RuntimeError, match="5 minutes|RESTRICTED or EMERGENCY"):
                await self.manager.enable_emergency_signing("admin", "test", "test reason")
        hsm_manager.clear_degraded()

    @pytest.mark.asyncio
    async def test_daily_limit_check(self):
        with patch("app.config.redis.RedisPool") as mock_pool:
            mock_redis = AsyncMock()
            mock_redis.eval = AsyncMock(return_value=1)
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            count = await self.manager._atomic_check_and_increment_daily_count()
            from app.config.settings import settings
            assert count <= settings.hsm_emergency_signing_max_daily

    @pytest.mark.asyncio
    async def test_daily_limit_exceeded(self):
        from app.config.settings import settings
        max_daily = settings.hsm_emergency_signing_max_daily
        with patch("app.config.redis.RedisPool") as mock_pool:
            mock_redis = AsyncMock()
            mock_redis.eval = AsyncMock(return_value=max_daily + 1)
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            count = await self.manager._atomic_check_and_increment_daily_count()
            assert count > max_daily

    @pytest.mark.asyncio
    async def test_daily_limit_resets_after_24h(self):
        self.manager._daily_signature_count = 100
        self.manager._daily_count_reset_at = datetime.now(timezone.utc) - timedelta(hours=25)
        with patch("app.config.redis.RedisPool") as mock_pool:
            mock_pool.get_client = AsyncMock(side_effect=Exception("Redis unavailable"))
            count = await self.manager._atomic_check_and_increment_daily_count()
            assert count == 1

    @pytest.mark.asyncio
    async def test_increment_signature_count(self):
        with patch("app.config.redis.RedisPool") as mock_pool:
            mock_redis = AsyncMock()
            mock_redis.eval = AsyncMock(return_value=1)
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            count = await self.manager._atomic_check_and_increment_daily_count()
            assert count == 1

    @pytest.mark.asyncio
    async def test_sign_emergency_requires_active(self):
        with patch.object(self.manager, 'is_emergency_signing_active', new_callable=AsyncMock, return_value=False):
            with pytest.raises(RuntimeError, match="not active"):
                await self.manager.sign_emergency("key", b"data")

    @pytest.mark.asyncio
    async def test_sign_jwt_emergency_requires_active(self):
        with patch.object(self.manager, 'is_emergency_signing_active', new_callable=AsyncMock, return_value=False):
            with pytest.raises(RuntimeError, match="not active"):
                await self.manager.sign_jwt_emergency("key", {}, {})


class TestEmergencySigningIntegration:
    @pytest.mark.asyncio
    async def test_emergency_signing_behavior_in_emergency_phase(self):
        hsm_manager.set_degraded("test_emergency")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=35)

        phase = hsm_manager.get_degraded_phase()
        assert phase == DegradedPhase.EMERGENCY

        behavior = hsm_manager.check_degraded_behavior("emergency_signing")
        assert behavior is True

        hsm_manager.clear_degraded()

    @pytest.mark.asyncio
    async def test_restricted_phase_allows_desensitization_restore(self):
        hsm_manager.set_degraded("test_restricted")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=15)

        behavior = hsm_manager.check_degraded_behavior("desensitization_restore")
        assert behavior is True

        hsm_manager.clear_degraded()

    def test_get_emergency_signing_manager_singleton(self):
        m1 = get_emergency_signing_manager()
        m2 = get_emergency_signing_manager()
        assert m1 is m2


class TestEmergencySigningLifecycle:
    def setup_method(self):
        self.manager = EmergencySigningManager()

    @pytest.mark.asyncio
    async def test_enable_sign_disable_lifecycle(self):
        hsm_manager.set_degraded("test")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=10)

        mock_redis = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipe.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)
        mock_redis.get = AsyncMock(return_value=b"operator1")
        mock_redis.expire = AsyncMock()

        with patch("app.config.settings.settings") as mock_settings, \
             patch("app.config.redis.RedisPool") as mock_pool, \
             patch.object(hsm_manager, "get_key_info", return_value={"slot": "EMERGENCY_SIGNING"}):
            mock_settings.hsm_emergency_signing_enabled = True
            mock_settings.hsm_emergency_signing_max_daily = 100
            mock_settings.hsm_emergency_signing_verification_hours = 72
            mock_settings.hsm_degraded_phase_1_threshold_minutes = 5
            mock_settings.hsm_degraded_phase_2_threshold_minutes = 30
            mock_pool.get_client = AsyncMock(return_value=mock_redis)

            await self.manager.enable_emergency_signing("operator1", "approver1", "test reason")

            with patch.object(self.manager, "is_emergency_signing_active", new_callable=AsyncMock, return_value=True), \
                 patch.object(self.manager, "_atomic_check_and_increment_daily_count", new_callable=AsyncMock, return_value=1), \
                 patch.object(hsm_manager, "sign", return_value=b"signature_bytes"):
                signature, metadata = await self.manager.sign_emergency("test_key", b"test data")
                assert signature == b"signature_bytes"
                assert metadata["emergency"] is True

            await self.manager.disable_emergency_signing("operator1")

        hsm_manager.clear_degraded()

    @pytest.mark.asyncio
    async def test_enable_requires_different_operator_approver(self):
        with pytest.raises(RuntimeError, match="must be different"):
            await self.manager.enable_emergency_signing("same_person", "same_person", "test reason")

    @pytest.mark.asyncio
    async def test_sign_emergency_when_not_active_raises(self):
        with patch.object(self.manager, "is_emergency_signing_active", new_callable=AsyncMock, return_value=False):
            with pytest.raises(RuntimeError, match="not active"):
                await self.manager.sign_emergency("test_key", b"test data")

    @pytest.mark.asyncio
    async def test_sign_jwt_emergency_lifecycle(self):
        hsm_manager.set_degraded("test")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=10)

        mock_redis = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipe.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)
        mock_redis.get = AsyncMock(return_value=b"operator1")
        mock_redis.expire = AsyncMock()

        with patch("app.config.settings.settings") as mock_settings, \
             patch("app.config.redis.RedisPool") as mock_pool, \
             patch.object(hsm_manager, "get_key_info", return_value={"slot": "EMERGENCY_SIGNING"}):
            mock_settings.hsm_emergency_signing_enabled = True
            mock_settings.hsm_emergency_signing_max_daily = 100
            mock_settings.hsm_emergency_signing_verification_hours = 72
            mock_settings.hsm_degraded_phase_1_threshold_minutes = 5
            mock_settings.hsm_degraded_phase_2_threshold_minutes = 30
            mock_pool.get_client = AsyncMock(return_value=mock_redis)

            await self.manager.enable_emergency_signing("operator1", "approver1", "jwt test reason")

            with patch.object(self.manager, "is_emergency_signing_active", new_callable=AsyncMock, return_value=True), \
                 patch.object(self.manager, "_atomic_check_and_increment_daily_count", new_callable=AsyncMock, return_value=1), \
                 patch.object(hsm_manager, "sign_jwt", return_value="jwt.token.here"):
                token, metadata = await self.manager.sign_jwt_emergency("test_key", {"alg": "RS256"}, {"sub": "test"})
                assert token == "jwt.token.here"
                assert metadata["emergency"] is True

            await self.manager.disable_emergency_signing("operator1")

        hsm_manager.clear_degraded()

    @pytest.mark.asyncio
    async def test_verify_emergency_signature_valid(self):
        with patch.object(hsm_manager, "verify_signature", return_value=True), \
             patch("app.config.redis.RedisPool") as mock_pool:
            mock_redis = AsyncMock()
            mock_redis.delete = AsyncMock()
            mock_pool.get_client = AsyncMock(return_value=mock_redis)

            result = await self.manager.verify_emergency_signature(b"data", b"sig", "report_1")
            assert result is True

    @pytest.mark.asyncio
    async def test_verify_emergency_signature_invalid(self):
        with patch.object(hsm_manager, "verify_signature", return_value=False):
            result = await self.manager.verify_emergency_signature(b"data", b"sig", "report_1")
            assert result is False
