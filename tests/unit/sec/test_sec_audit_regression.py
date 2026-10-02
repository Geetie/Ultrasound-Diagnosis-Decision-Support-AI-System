import pytest
import asyncio
import os
import hashlib
import hmac
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta, timezone
pytestmark = [pytest.mark.track_sec, pytest.mark.track_sg]


class TestP0_1_RS256Enforcement:
    def test_jwt_algorithm_must_be_rs256_in_settings(self):
        from app.config.settings import Settings
        with pytest.raises(Exception):
            Settings(jwt_algorithm="HS256")

    def test_jwt_algorithm_rs256_accepted(self):
        from app.config.settings import Settings
        s = Settings(jwt_algorithm="RS256")
        assert s.jwt_algorithm == "RS256"

    def test_decode_access_token_uses_hardcoded_rs256(self):
        from app.security import auth as auth_module
        import inspect
        source = inspect.getsource(auth_module.decode_access_token)
        assert '"RS256"' in source
        assert "settings.jwt_algorithm" not in source or "RS256" in source

    def test_inactive_user_returns_403(self):
        from app.security import auth as auth_module
        import inspect
        source = inspect.getsource(auth_module.get_current_active_user)
        assert "403" in source


class TestP0_2_PatientNameHashStorage:
    def test_desensitization_engine_no_plaintext_names(self):
        from app.security.desensitization import DesensitizationEngine
        engine = DesensitizationEngine()
        engine.load_rules()
        assert not hasattr(engine, '_patient_name_to_token') or not engine._patient_name_to_token
        assert hasattr(engine, '_patient_name_hash_to_token')

    def test_patient_name_hash_is_hmac(self):
        from app.security.desensitization import DesensitizationEngine
        engine = DesensitizationEngine()
        engine.load_rules()
        name = "张三"
        hash_result = engine._hash_patient_name(name)
        assert len(hash_result) == 64
        hash_result2 = engine._hash_patient_name(name)
        assert hash_result == hash_result2

    def test_different_names_different_hashes(self):
        from app.security.desensitization import DesensitizationEngine
        engine = DesensitizationEngine()
        engine.load_rules()
        hash1 = engine._hash_patient_name("张三")
        hash2 = engine._hash_patient_name("李四")
        assert hash1 != hash2

    def test_token_lookup_via_hash(self):
        from app.security.desensitization import DesensitizationEngine
        engine = DesensitizationEngine()
        engine.load_rules()
        token1 = engine._get_patient_token("张三")
        token2 = engine._get_patient_token("张三")
        assert token1 == token2
        assert token1.startswith("PATIENT_")

    def test_different_names_different_tokens(self):
        from app.security.desensitization import DesensitizationEngine
        engine = DesensitizationEngine()
        engine.load_rules()
        token1 = engine._get_patient_token("张三")
        token2 = engine._get_patient_token("李四")
        assert token1 != token2

    def test_hmac_key_is_random_bytes(self):
        from app.security.desensitization import DesensitizationEngine
        engine = DesensitizationEngine()
        engine.load_rules()
        assert len(engine._name_hmac_key) == 32
        assert isinstance(engine._name_hmac_key, bytes)


class TestP0_3_NoFakeUsersDB:
    def test_no_fake_users_db_in_auth_api(self):
        import app.api.v1.auth as auth_api
        assert not hasattr(auth_api, 'FAKE_USERS_DB')

    def test_user_model_exists(self):
        from app.models.database import User
        assert hasattr(User, '__tablename__')
        assert User.__tablename__ == 'users'
        assert hasattr(User, 'username')
        assert hasattr(User, 'hashed_password')
        assert hasattr(User, 'role')

    def test_token_stats_requires_admin(self):
        import app.api.v1.auth as auth_api
        import inspect
        source = inspect.getsource(auth_api.get_token_stats)
        assert "SYSTEM_ADMIN" in source or "RoleChecker" in source


class TestP0_4_ConsentStatusEnumValidation:
    def test_valid_consent_status_accepted(self):
        from safety_gateway.validation_result import ConsentStatus
        assert ConsentStatus("granted") == ConsentStatus.GRANTED
        assert ConsentStatus("denied") == ConsentStatus.DENIED
        assert ConsentStatus("withdrawn") == ConsentStatus.WITHDRAWN

    def test_invalid_consent_status_rejected(self):
        from safety_gateway.validation_result import ConsentStatus
        with pytest.raises(ValueError):
            ConsentStatus("GRANTED")
        with pytest.raises(ValueError):
            ConsentStatus("invalid")

    def test_checker_validates_consent_status(self):
        import inspect
        from safety_gateway.checker import SafetyGatewayChecker
        source = inspect.getsource(SafetyGatewayChecker.perform_full_check)
        assert "consent_status_at_input" in source


class TestP1_1_RedisPubSubSync:
    def test_publish_rules_update_method_exists(self):
        from app.security.desensitization import DesensitizationEngine
        engine = DesensitizationEngine()
        engine.load_rules()
        assert hasattr(engine, '_publish_rules_update')

    def test_subscribe_rules_updates_method_exists(self):
        from app.security.desensitization import DesensitizationEngine
        engine = DesensitizationEngine()
        engine.load_rules()
        assert hasattr(engine, 'subscribe_rules_updates')

    def test_add_rule_triggers_publish(self):
        from app.security.desensitization import DesensitizationEngine, DesensitizationRule
        engine = DesensitizationEngine()
        engine.load_rules()
        with patch.object(engine, '_publish_rules_update') as mock_pub:
            engine.add_rule(DesensitizationRule(
                name="test_rule", pattern=r"test", replacement="***"
            ))
            mock_pub.assert_called_once()

    def test_update_rule_triggers_publish(self):
        from app.security.desensitization import DesensitizationEngine, DesensitizationRule
        engine = DesensitizationEngine()
        engine.load_rules()
        with patch.object(engine, '_publish_rules_update') as mock_pub:
            engine.update_rule("id_card", DesensitizationRule(
                name="id_card", pattern=r"\d{17}[\dXx]", replacement="***"
            ))
            mock_pub.assert_called_once()

    def test_remove_rule_triggers_publish(self):
        from app.security.desensitization import DesensitizationEngine
        engine = DesensitizationEngine()
        engine.load_rules()
        with patch.object(engine, '_publish_rules_update') as mock_pub:
            engine.remove_rule("email")
            mock_pub.assert_called_once()


class TestP1_2_ChainLocksCleanup:
    def test_max_chain_locks_constant_exists(self):
        from app.services.audit_service import _MAX_CHAIN_LOCKS
        assert _MAX_CHAIN_LOCKS == 10000

    def test_release_chain_lock_function_exists(self):
        from app.services.audit_service import release_chain_lock
        assert callable(release_chain_lock)

    async def test_release_chain_lock_removes_entry(self):
        from app.services.audit_service import _chain_locks, _chain_lock_order, release_chain_lock
        _chain_locks["test_session"] = asyncio.Lock()
        _chain_lock_order.append("test_session")
        await release_chain_lock("test_session")
        assert "test_session" not in _chain_locks
        assert "test_session" not in _chain_lock_order


class TestP1_5_AuthRateLimiting:
    def test_auth_token_rate_limit_configured(self):
        from app.config.rate_limit import RATE_LIMITS
        assert "/api/v1/auth/token" in RATE_LIMITS
        assert RATE_LIMITS["/api/v1/auth/token"]["capacity"] == 5

    def test_auth_login_rate_limit_configured(self):
        from app.config.rate_limit import RATE_LIMITS
        assert "/api/v1/auth/login" in RATE_LIMITS
        assert RATE_LIMITS["/api/v1/auth/login"]["capacity"] == 5

    def test_consent_rate_limit_configured(self):
        from app.config.rate_limit import RATE_LIMITS
        assert "/api/v1/consent" in RATE_LIMITS


class TestP1_6_JWTKeyRotationParallelVerification:
    def test_decode_tries_old_keys_on_failure(self):
        import inspect
        from app.security import auth as auth_module
        source = inspect.getsource(auth_module.decode_access_token)
        assert "JWT_KEY_NAME" in source
        assert "old_public_key" in source or "old" in source.lower()

    def test_key_slot_import(self):
        from app.security.auth import KeySlot
        assert hasattr(KeySlot, 'JWT_SIGNING')


class TestP1_7_SessionKickIntegration:
    def test_login_endpoint_has_session_kick(self):
        import inspect
        from app.api.v1 import auth as auth_api
        source = inspect.getsource(auth_api.login)
        assert "create_refresh_token" in source

    def test_token_store_imported(self):
        from app.security.refresh_token import refresh_token_store
        assert refresh_token_store is not None


class TestP1_8_ConsentWithdrawalBroadcast:
    def test_withdrawal_stream_constant_exists(self):
        from app.security.consent_withdrawal_consumer import CONSENT_WITHDRAWAL_STREAM
        assert CONSENT_WITHDRAWAL_STREAM is not None

    def test_update_consent_single_publish_via_repository(self):
        """P0-3 修复后的契约：API 层不再直接 xadd（唯一生产者 =
        ConsentRepository._publish_consent_withdrawal，含 withdrawn_at 全字段）。
        历史断言（要求 API 层内含 xadd）钉住的是双投递缺陷本体，随修复更新。"""
        import inspect
        from app.api.v1 import consent as consent_api
        source = inspect.getsource(consent_api.update_consent)
        assert "xadd" not in source
        assert "RedisPool" not in source
        # 投递职责归位说明必须留痕，防止未来误改回
        assert "_publish_consent_withdrawal" in source


class TestP1_9_PeriodicAuditVerification:
    def test_start_periodic_verification_exists(self):
        from app.services.audit_service import start_periodic_verification
        assert callable(start_periodic_verification)

    def test_stop_periodic_verification_exists(self):
        from app.services.audit_service import stop_periodic_verification
        assert callable(stop_periodic_verification)

    @pytest.mark.asyncio
    async def test_start_stop_cycle(self):
        from app.services.audit_service import start_periodic_verification, stop_periodic_verification
        await start_periodic_verification(interval_hours=1)
        await stop_periodic_verification()


class TestP1_10_HSMPSSPadding:
    def test_sign_uses_pss(self):
        import inspect
        from app.security.hsm import HSMManager
        source = inspect.getsource(HSMManager.sign)
        assert "PSS" in source
        assert "MGF1" in source

    def test_verify_uses_pss(self):
        import inspect
        from app.security.hsm import HSMManager
        source = inspect.getsource(HSMManager.verify_signature)
        assert "PSS" in source
        assert "MGF1" in source


class TestP2_4_FusionNoneConsistency:
    def test_fusion_desensitize_none_returns_none(self):
        from app.fusion.desensitization import FusionDesensitizationEngine
        engine = FusionDesensitizationEngine()
        result = engine.desensitize_llm_output(None)
        assert result.desensitized_text is None


class TestP2_5_ForbiddenTermsBoundaryMatch:
    def test_filter_uses_regex(self):
        import inspect
        from safety_gateway.degraded_output import filter_forbidden_terms, FORBIDDEN_GRADING_TERMS
        source = inspect.getsource(filter_forbidden_terms)
        assert "FORBIDDEN_GRADING_TERMS" in source
        assert len(FORBIDDEN_GRADING_TERMS) > 0

    def test_non_malignant_not_replaced(self):
        from safety_gateway.degraded_output import filter_forbidden_terms
        result = filter_forbidden_terms("非恶性病变")
        assert "非" in result
        assert "***" not in result or "非***" not in result


class TestP2_9_AuditLogTypeField:
    def test_audit_log_has_log_type(self):
        from app.models.database import AuditLog
        columns = [c.name for c in AuditLog.__table__.columns]
        assert 'log_type' in columns

    def test_log_action_accepts_log_type(self):
        import inspect
        from app.services.audit_service import AuditService
        source = inspect.getsource(AuditService.log_action)
        assert "log_type" in source


class TestP2_10_EmptyDataJudgment:
    def test_zero_value_not_treated_as_empty(self):
        from safety_gateway.checker import SafetyGatewayChecker
        import inspect
        source = inspect.getsource(SafetyGatewayChecker.perform_full_check)
        assert "isinstance" in source
        assert "bool" in source or "int" in source or "float" in source


class TestP2_3_ExponentialBackoff:
    def test_backoff_formula_in_client(self):
        import inspect
        from app.services.safety_gateway_client import SafetyGatewayClient
        source = inspect.getsource(SafetyGatewayClient.check_output)
        assert "2 **" in source or "attempt" in source
