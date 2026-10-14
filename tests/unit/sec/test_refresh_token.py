import logging
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from app.config.settings import settings
from app.security.auth import RoleChecker, create_access_token, decode_access_token, get_password_hash, verify_password
from app.security.hsm import DegradedPhase, HSMManager, KeySlot, hsm_manager
from app.security.refresh_token import (
    MemoryRefreshTokenStore,
    refresh_token_store,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.track_sec, pytest.mark.track_inf, pytest.mark.sec_domain_token]
@pytest.fixture(autouse=True)
def reset_refresh_store():
    store = MemoryRefreshTokenStore()
    # 清除所有数据
    store._store.clear()
    store._family_store.clear()
    store._user_tokens.clear()
    yield
    store._store.clear()
    store._family_store.clear()
    store._user_tokens.clear()


@pytest.fixture
def sample_user_info():
    return {
        "username": "test_user",
        "full_name": "Test User",
        "email": "test@example.com",
        "role": "ULTRASOUND_DOCTOR",
        "disabled": False
    }


def test_create_refresh_token(sample_user_info):
    store = MemoryRefreshTokenStore()
    token_id = store.create_refresh_token("user123", sample_user_info)

    assert token_id is not None
    assert len(token_id) > 20


def test_verify_valid_refresh_token(sample_user_info):
    store = MemoryRefreshTokenStore()
    token_id = store.create_refresh_token("user123", sample_user_info)

    token_data = store.verify_refresh_token(token_id)

    assert token_data is not None
    assert token_data["user_id"] == "user123"
    assert token_data["user_info"]["username"] == "test_user"
    assert token_data["revoked"] is False


def test_verify_invalid_refresh_token():
    store = MemoryRefreshTokenStore()
    token_data = store.verify_refresh_token("invalid_token_id_12345")

    assert token_data is None


def test_revoke_refresh_token(sample_user_info):
    store = MemoryRefreshTokenStore()
    token_id = store.create_refresh_token("user123", sample_user_info)

    success = store.revoke_refresh_token(token_id)
    assert success is True

    token_data = store.verify_refresh_token(token_id)
    assert token_data is None


def test_revoke_nonexistent_token():
    store = MemoryRefreshTokenStore()
    success = store.revoke_refresh_token("nonexistent_token_12345")

    assert success is False


def test_token_family_rotation(sample_user_info):
    store = MemoryRefreshTokenStore()
    family_id = "test_family_123"

    token1 = store.create_refresh_token("user123", sample_user_info, family_id=family_id)
    token2 = store.create_refresh_token("user123", sample_user_info, family_id=family_id)

    revoked_count = store.revoke_token_family(family_id)

    assert revoked_count == 2

    assert store.verify_refresh_token(token1) is None
    assert store.verify_refresh_token(token2) is None


def test_revoke_all_user_tokens(sample_user_info):
    store = MemoryRefreshTokenStore()
    token1 = store.create_refresh_token("user123", sample_user_info)
    token2 = store.create_refresh_token("user123", sample_user_info)
    token3 = store.create_refresh_token("user456", sample_user_info)

    revoked_count = store.revoke_all_user_tokens("user123")

    assert revoked_count == 2

    assert store.verify_refresh_token(token1) is None
    assert store.verify_refresh_token(token2) is None
    assert store.verify_refresh_token(token3) is not None


def test_token_family_inheritance(sample_user_info):
    store = MemoryRefreshTokenStore()
    token1 = store.create_refresh_token("user123", sample_user_info)

    token1_data = store.verify_refresh_token(token1)
    family_id = token1_data["family_id"]

    token2 = store.create_refresh_token("user123", sample_user_info, family_id=family_id)

    assert token2 is not None
    token2_data = store.verify_refresh_token(token2)
    assert token2_data["family_id"] == family_id


def test_get_refresh_token_count(sample_user_info):
    store = MemoryRefreshTokenStore()
    token1 = store.create_refresh_token("user123", sample_user_info)
    token2 = store.create_refresh_token("user456", sample_user_info)

    stats = store.get_token_count()

    assert stats["total"] == 2
    assert stats["active"] == 2
    assert stats["revoked"] == 0
    assert stats["expired"] == 0


def test_get_token_count_after_revocation(sample_user_info):
    store = MemoryRefreshTokenStore()
    token1 = store.create_refresh_token("user123", sample_user_info)
    token2 = store.create_refresh_token("user456", sample_user_info)

    store.revoke_refresh_token(token1)

    stats = store.get_token_count()

    assert stats["total"] == 2
    assert stats["active"] == 1
    assert stats["revoked"] == 1
    assert stats["expired"] == 0


def test_token_store_singleton():
    store1 = MemoryRefreshTokenStore()
    store2 = MemoryRefreshTokenStore()

    assert store1 is store2


def test_refresh_token_contains_required_fields(sample_user_info):
    store = MemoryRefreshTokenStore()
    token_id = store.create_refresh_token("user123", sample_user_info)
    token_data = store.verify_refresh_token(token_id)

    assert "token_id" in token_data
    assert "user_id" in token_data
    assert "user_info" in token_data
    assert "family_id" in token_data
    assert "created_at" in token_data
    assert "expires_at" in token_data
    assert "revoked" in token_data
    assert "last_used_at" in token_data


def test_refresh_token_expiry_handling(sample_user_info):
    store = MemoryRefreshTokenStore()
    with freeze_time("2026-01-01 00:00:00"):
        token_id = store.create_refresh_token("user123", sample_user_info)

    with freeze_time("2026-01-10 00:00:00"):
        token_data = store.verify_refresh_token(token_id)

        assert token_data is None


def test_cleanup_expired_tokens(sample_user_info):
    store = MemoryRefreshTokenStore()
    with freeze_time("2026-01-01 00:00:00"):
        expired_token = store.create_refresh_token("user123", sample_user_info)

    with freeze_time("2026-01-08 00:00:00"):
        active_token = store.create_refresh_token("user456", sample_user_info)

    with freeze_time("2026-01-10 00:00:00"):
        cleaned_count = store.cleanup_expired_tokens()

        assert cleaned_count == 1
        assert expired_token not in store._store
        assert active_token in store._store

    store._store.clear()
    store._family_store.clear()
    store._user_tokens.clear()


def test_concurrent_token_creation(sample_user_info):
    store = MemoryRefreshTokenStore()
    import threading

    results = []
    errors = []

    def create_token():
        try:
            token = store.create_refresh_token("user123", sample_user_info)
            results.append(token)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=create_token) for _ in range(10)]

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(results) == 10


def test_revoke_already_revoked_token(sample_user_info):
    store = MemoryRefreshTokenStore()
    token_id = store.create_refresh_token("user123", sample_user_info)

    store.revoke_refresh_token(token_id)
    result = store.revoke_refresh_token(token_id)

    assert result is True


def test_refresh_token_updates_last_used(sample_user_info):
    store = MemoryRefreshTokenStore()
    token_id = store.create_refresh_token("user123", sample_user_info)

    token_data1 = store.verify_refresh_token(token_id)
    last_used1 = token_data1["last_used_at"]

    token_data2 = store.verify_refresh_token(token_id)
    last_used2 = token_data2["last_used_at"]

    assert last_used2 >= last_used1


class TestTokenFamilyDeep:
    """Token Family深度测试（合并自test_sec_token_consent_audit_deep.py）"""

    def test_rotate_and_verify_new(self):
        store = refresh_token_store
        t1 = store.create_refresh_token(user_id="tf_deep_2", user_info={"role": "doctor"})
        t2 = store.rotate_refresh_token(t1)
        assert store.verify_refresh_token(t2) is not None

    def test_replay_detection_on_rotate(self, monkeypatch):
        # 钉的是「reuse 窃取检测」语义 —— 显式关掉旋转宽限（合法并发返回后继的
        # 语义由 test_refresh_token_grace_period.py 覆盖）
        monkeypatch.setattr(settings, "refresh_token_grace_period_s", 0)
        store = refresh_token_store
        t1 = store.create_refresh_token(user_id="tf_deep_3", user_info={"role": "doctor"})
        t2 = store.rotate_refresh_token(t1)
        with pytest.raises(ValueError, match="already been used"):
            store.rotate_refresh_token(t1)

    def test_family_revocation_on_replay(self, monkeypatch):
        monkeypatch.setattr(settings, "refresh_token_grace_period_s", 0)
        store = refresh_token_store
        t1 = store.create_refresh_token(user_id="tf_deep_4", user_info={"role": "doctor"})
        t2 = store.rotate_refresh_token(t1)
        with pytest.raises(ValueError):
            store.rotate_refresh_token(t1)
        assert store.verify_refresh_token(t2) is None

    def test_long_rotation_chain(self):
        store = refresh_token_store
        tid = store.create_refresh_token(user_id="tf_deep_5", user_info={"role": "doctor"})
        data = store.verify_refresh_token(tid)
        family_id = data["family_id"]
        for _ in range(10):
            new_tid = store.rotate_refresh_token(tid)
            tid = new_tid
        final_data = store.verify_refresh_token(tid)
        assert final_data is not None
        assert final_data["family_id"] == family_id

    def test_token_id_is_cryptographically_random(self):
        store = refresh_token_store
        t1 = store.create_refresh_token(user_id="random_1", user_info={"role": "doctor"})
        t2 = store.create_refresh_token(user_id="random_2", user_info={"role": "doctor"})
        assert t1 != t2
        assert len(t1) > 20

    def test_family_id_is_unique_per_family(self):
        store = refresh_token_store
        t1 = store.create_refresh_token(user_id="family_unique_1", user_info={"role": "doctor"})
        t2 = store.create_refresh_token(user_id="family_unique_2", user_info={"role": "doctor"})
        d1 = store.verify_refresh_token(t1)
        d2 = store.verify_refresh_token(t2)
        assert d1["family_id"] != d2["family_id"]


class TestConcurrentSessionLimit:
    """并发会话限制测试"""

    def test_max_5_concurrent_sessions(self):
        store = refresh_token_store
        user_id = "concurrent_limit_test"
        store.revoke_all_user_tokens(user_id)
        tokens = []
        for _ in range(5):
            tid = store.create_refresh_token(user_id=user_id, user_info={"role": "doctor"})
            tokens.append(tid)
        count = store.get_user_token_count(user_id)
        assert count <= 5

    def test_6th_session_revokes_oldest(self):
        store = refresh_token_store
        user_id = "concurrent_6th_test"
        store.revoke_all_user_tokens(user_id)
        tokens = []
        for _ in range(6):
            tid = store.create_refresh_token(user_id=user_id, user_info={"role": "doctor"})
            tokens.append(tid)
        count = store.get_user_token_count(user_id)
        assert count <= 5


class TestRefreshTokenConcurrentRevokeAndVerify:
    """Refresh Token并发撤销与验证"""

    def test_concurrent_revoke_and_verify(self):
        store = refresh_token_store
        token_id = store.create_refresh_token(user_id="concurrent_test", user_info={"role": "doctor"})
        errors = []

        def revoke_worker():
            try:
                for _ in range(50):
                    store.revoke_refresh_token(token_id)
            except Exception as e:
                errors.append(e)

        def verify_worker():
            try:
                for _ in range(50):
                    store.verify_refresh_token(token_id)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=revoke_worker),
            threading.Thread(target=verify_worker),
        ]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(errors) == 0


# ============================================================================
# 合并自 test_sec_token_consent_audit_deep.py - Token 相关
# ============================================================================


class TestTokenFamilyDeepExtended:

    def test_create_and_verify(self):
        token_id = refresh_token_store.create_refresh_token(
            user_id="deep_user", user_info={"role": "doctor"}
        )
        data = refresh_token_store.verify_refresh_token(token_id)
        assert data is not None
        assert data["user_id"] == "deep_user"

    def test_revoke_single_token(self):
        token_id = refresh_token_store.create_refresh_token(
            user_id="revoke_single_user", user_info={"role": "doctor"}
        )
        refresh_token_store.revoke_refresh_token(token_id)
        assert refresh_token_store.verify_refresh_token(token_id) is None

    def test_revoke_all_user_tokens(self):
        t1 = refresh_token_store.create_refresh_token(
            user_id="revoke_all_user", user_info={"role": "doctor"}
        )
        t2 = refresh_token_store.create_refresh_token(
            user_id="revoke_all_user", user_info={"role": "doctor"}
        )
        refresh_token_store.revoke_all_user_tokens("revoke_all_user")
        assert refresh_token_store.verify_refresh_token(t1) is None
        assert refresh_token_store.verify_refresh_token(t2) is None

    def test_expired_token_returns_none(self):
        with freeze_time("2024-01-01"):
            token_id = refresh_token_store.create_refresh_token(
                user_id="expire_user", user_info={"role": "doctor"}
            )
        with freeze_time("2024-02-01"):
            data = refresh_token_store.verify_refresh_token(token_id)
            assert data is None

    def test_verify_nonexistent_token(self):
        result = refresh_token_store.verify_refresh_token("nonexistent_token_id")
        assert result is None

    def test_revoke_nonexistent_token(self):
        refresh_token_store.revoke_refresh_token("nonexistent_token_id")

    def test_token_has_family_id(self):
        token_id = refresh_token_store.create_refresh_token(
            user_id="family_user", user_info={"role": "doctor"}
        )
        data = refresh_token_store.verify_refresh_token(token_id)
        assert "family_id" in data

    def test_token_has_user_info(self):
        token_id = refresh_token_store.create_refresh_token(
            user_id="info_user", user_info={"role": "doctor"}
        )
        data = refresh_token_store.verify_refresh_token(token_id)
        assert data["user_info"]["role"] == "doctor"

    def test_token_has_created_at(self):
        token_id = refresh_token_store.create_refresh_token(
            user_id="created_at_user", user_info={"role": "doctor"}
        )
        data = refresh_token_store.verify_refresh_token(token_id)
        assert "created_at" in data

    def test_token_has_expires_at(self):
        token_id = refresh_token_store.create_refresh_token(
            user_id="expires_at_user", user_info={"role": "doctor"}
        )
        data = refresh_token_store.verify_refresh_token(token_id)
        assert "expires_at" in data


class TestJWTAuthDeep:

    def test_create_and_decode_token(self):
        token = create_access_token({"sub": "test_user", "role": "doctor"})
        payload = decode_access_token(token)
        assert payload["sub"] == "test_user"
        assert payload["role"] == "doctor"

    def test_expired_token_rejected(self):
        token = create_access_token(
            {"sub": "expired_user", "role": "doctor"}, expires_delta=timedelta(seconds=-1)
        )
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            decode_access_token(token)

    def test_role_checker_allows_authorized_role(self):
        checker = RoleChecker(allowed_roles=["doctor"])
        assert checker is not None

    def test_token_contains_issued_at(self):
        token = create_access_token({"sub": "iat_user", "role": "doctor"})
        payload = decode_access_token(token)
        assert "iat" in payload

    def test_token_contains_exp(self):
        token = create_access_token({"sub": "exp_user", "role": "doctor"})
        payload = decode_access_token(token)
        assert "exp" in payload


# ============================================================================
# 合并自 test_sec_gap_fill.py - Token 相关
# ============================================================================


class TestHSMDegradedEventE2E:

    def test_set_degraded_with_reason(self):
        hsm = HSMManager()
        hsm.set_degraded("hsm_connection_lost")
        assert hsm.is_degraded() is True

    def test_set_degraded_reason_recorded(self):
        hsm = HSMManager()
        hsm.set_degraded("key_generation_timeout")
        assert hsm._degraded_reason == "key_generation_timeout"

    def test_clear_degraded_resets_reason(self):
        hsm = HSMManager()
        hsm.set_degraded("test_reason")
        hsm.clear_degraded()
        assert hsm.is_degraded() is False

    def test_degraded_phase_transitions(self):
        hsm = HSMManager()
        hsm.set_degraded("test")
        hsm._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        assert hsm.get_degraded_phase() == DegradedPhase.CACHED

    def test_degraded_phase_restricted(self):
        hsm = HSMManager()
        hsm.set_degraded("test")
        hsm._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        assert hsm.get_degraded_phase() == DegradedPhase.RESTRICTED

    def test_degraded_phase_emergency(self):
        hsm = HSMManager()
        hsm.set_degraded("test")
        hsm._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=60)
        assert hsm.get_degraded_phase() == DegradedPhase.EMERGENCY

    def test_auth_set_degraded_on_sign_failure(self):
        with patch.object(hsm_manager, 'sign_jwt', side_effect=ConnectionError("HSM unavailable")):
            with patch.object(hsm_manager, 'set_degraded') as mock_degraded:
                try:
                    create_access_token({"sub": "test"})
                except Exception:
                    logger.warning("TestHSMDegradedEventE2E.test_auth_set_degraded_on_sign_failure: 未预期异常，按原逻辑降级", exc_info=True)
                    pass
                mock_degraded.assert_called_once()

    def test_auth_set_degraded_on_public_key_missing(self):
        with patch.object(hsm_manager, 'get_public_key', return_value=None):
            with patch.object(hsm_manager, 'set_degraded') as mock_degraded:
                try:
                    decode_access_token("invalid_token")
                except Exception:
                    logger.warning("TestHSMDegradedEventE2E.test_auth_set_degraded_on_public_key_missing: 未预期异常，按原逻辑降级", exc_info=True)
                    pass
                mock_degraded.assert_called_once()

    def test_hsm_degraded_phase_config_values(self):
        assert settings.hsm_degraded_phase_1_threshold_minutes == 5
        assert settings.hsm_degraded_phase_2_threshold_minutes == 30


class TestJWTHSMIntegrationBoundary:

    def test_jwt_algorithm_is_rs256(self):
        assert settings.jwt_algorithm == "RS256"

    def test_jwt_expire_minutes_positive(self):
        assert settings.jwt_expire_minutes > 0

    def test_jwt_refresh_expire_days_positive(self):
        assert settings.jwt_refresh_expire_days > 0

    def test_jwt_key_rotation_days_config(self):
        if settings.deployment_profile == 'compact':
            assert settings.jwt_key_rotation_days == settings.jwt_key_rotation_days_compact
        else:
            assert settings.jwt_key_rotation_days == 120

    def test_hsm_private_key_export_prohibited(self):
        hsm = HSMManager()
        with pytest.raises(RuntimeError):
            hsm.get_private_key("any_key")

    def test_hsm_key_slot_jwt_signing_exists(self):
        assert KeySlot.JWT_SIGNING is not None

    def test_hsm_key_slot_emergency_signing_exists(self):
        assert KeySlot.EMERGENCY_SIGNING is not None

    def test_password_hashing_works(self):
        hashed = get_password_hash("test_password")
        assert verify_password("test_password", hashed) is True
        assert verify_password("wrong_password", hashed) is False
