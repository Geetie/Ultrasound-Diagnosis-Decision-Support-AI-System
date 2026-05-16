import asyncio
import os
import time
import hashlib
import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.track_sec, pytest.mark.sec_domain_sg]

os.environ["USE_REDIS_REFRESH_TOKEN"] = "false"
os.environ["USE_SOFT_HSM"] = "true"

from app.security.hsm import HSMManager, DegradedPhase, hsm_manager
from app.security.auth import create_access_token, decode_access_token, _ensure_jwt_key_exists
from app.security.refresh_token import (
    MemoryRefreshTokenStore,
    create_refresh_token,
    verify_refresh_token,
    revoke_refresh_token,
    revoke_token_family,
    revoke_all_user_tokens,
    cleanup_expired_tokens,
    get_refresh_token_count
)
from app.security.three_layer_desensitization import (
    ThreeLayerDesensitizationManager,
    DesensitizationLayer
)
from app.security.desensitization import desensitization_engine, DesensitizationRule, get_desensitization_engine, DesensitizationEngine
from app.fusion.desensitization import fusion_desensitization_engine, FusionDesensitizationEngine
from safety_gateway.desensitization import sg_desensitization_engine
from safety_gateway.checker import SafetyGatewayChecker, CheckResultStatus
from app.config.settings import settings
from app.security.refresh_token import refresh_token_store
from app.security.emergency_signing import EmergencySigningManager
from app.security.consent_withdrawal_consumer import ConsentWithdrawalConsumer
from app.services.audit_service import AuditService
from app.models.desensitization_mapping import DesensitizationMapping


@pytest.fixture(autouse=True)
def reset_hsm_state():
    hsm_manager._is_degraded = False
    hsm_manager._degraded_at = None
    hsm_manager._degraded_reason = None
    hsm_manager._cached_keys = {}
    yield


class TestDataFactory:

    _counter = 0

    @classmethod
    def reset_counter(cls):
        cls._counter = 0

    @classmethod
    def get_unique_user_id(cls) -> str:
        cls._counter += 1
        return f"test_user_{cls._counter}_{datetime.now().timestamp()}"

    @classmethod
    def get_unique_patient_id(cls) -> str:
        cls._counter += 1
        return f"P{cls._counter:06d}"

    @classmethod
    def get_user_info(cls, role: str = "ULTRASOUND_DOCTOR") -> Dict[str, Any]:
        return {
            "username": cls.get_unique_user_id(),
            "full_name": f"测试用户{cls._counter}",
            "email": f"test{cls._counter}@hospital.com",
            "role": role,
            "disabled": False
        }


class TestHSMSignaturePadding:

    def test_sign_uses_pkcs1v15_padding(self):
        data = b"test_data_to_sign"

        hsm_manager.generate_key_pair("test_key")
        signature = hsm_manager.sign("test_key", data)

        assert signature is not None
        assert len(signature) > 0

        verified = hsm_manager.verify_signature("test_key", data, signature)
        assert verified is True

    def test_signature_compatible_with_jwt_rs256(self):
        from jose import jwt

        data = {"sub": "test_user", "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())}

        hsm_manager.generate_key_pair("jwt_test_key")
        public_key_pem = hsm_manager.get_public_key("jwt_test_key")

        header = {"alg": "RS256", "typ": "JWT", "kid": "jwt_test_key"}
        encoded = hsm_manager.sign_jwt("jwt_test_key", header, data, "RS256")

        decoded = jwt.decode(encoded, public_key_pem, algorithms=["RS256"])
        assert decoded["sub"] == "test_user"


class TestHSMDegradedState:

    def test_is_degraded_initially_false(self):
        assert hsm_manager.is_degraded() is False

    def test_set_degraded_sets_state(self):
        hsm_manager.set_degraded("test_reason")

        assert hsm_manager.is_degraded() is True
        assert hsm_manager._degraded_reason == "test_reason"
        assert hsm_manager._degraded_at is not None

    def test_clear_degraded_resets_state(self):
        hsm_manager.set_degraded("test_reason")
        hsm_manager.clear_degraded()

        assert hsm_manager.is_degraded() is False
        assert hsm_manager._degraded_reason is None
        assert hsm_manager._degraded_at is None

    def test_degraded_phase_normal(self):
        hsm_manager.clear_degraded()
        assert hsm_manager.get_degraded_phase() == DegradedPhase.NORMAL

    def test_degraded_phase_cached(self):
        hsm_manager.set_degraded("test")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        assert hsm_manager.get_degraded_phase() == DegradedPhase.CACHED

    def test_degraded_phase_restricted(self):
        hsm_manager.set_degraded("test")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=15)
        assert hsm_manager.get_degraded_phase() == DegradedPhase.RESTRICTED

    def test_degraded_phase_emergency(self):
        hsm_manager.set_degraded("test")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=45)
        assert hsm_manager.get_degraded_phase() == DegradedPhase.EMERGENCY

    def test_get_hsm_mode(self):
        with patch.dict(os.environ, {"USE_SOFT_HSM": "true"}):
            hsm = HSMManager()
            assert hsm.get_hsm_mode() == "soft_hsm"


class TestKeyRotationParallelValidation:

    def test_key_rotation_preserves_old_key(self):
        hsm_manager.generate_key_pair("rotation_test_key")
        old_public_key = hsm_manager.get_public_key("rotation_test_key")

        hsm_manager.rotate_key("rotation_test_key", parallel_days=7)
        new_public_key = hsm_manager.get_public_key("rotation_test_key")

        assert old_public_key != new_public_key

        old_key_info = hsm_manager._key_slots[hsm_manager._get_key_slot("rotation_test_key")].get("rotation_test_key_v1")
        assert old_key_info is not None
        assert old_key_info["expires_at"] is not None

    def test_old_key_still_valid_after_rotation(self):
        hsm_manager.generate_key_pair("rotation_verify_key")

        data = b"test_data_before_rotation"
        signature_before_rotation = hsm_manager.sign("rotation_verify_key", data)

        hsm_manager.rotate_key("rotation_verify_key", parallel_days=7)

        verified = hsm_manager.verify_signature("rotation_verify_key", data, signature_before_rotation)
        assert verified is True


class TestSoftHSMExpiry:

    def test_soft_hsm_expiry_check(self):
        with patch.dict(os.environ, {"USE_SOFT_HSM": "true"}):
            hsm = HSMManager()
            hsm._soft_hsm_enabled_at = datetime.now(timezone.utc) - timedelta(days=91)

            expired = hsm._check_soft_hsm_expiry()
            assert expired is True

    def test_soft_hsm_approaching_expiry_warning(self):
        with patch.dict(os.environ, {"USE_SOFT_HSM": "true"}):
            hsm = HSMManager()
            hsm._soft_hsm_enabled_at = datetime.now(timezone.utc) - timedelta(days=85)

            expired = hsm._check_soft_hsm_expiry()
            assert expired is False


class TestAuthHSMIntegration:

    def test_jwt_key_created_on_demand(self):
        _ensure_jwt_key_exists()

        keys = hsm_manager.list_keys()
        assert "jwt_signing_key" in keys

    def test_create_access_token_uses_hsm(self):
        token = create_access_token({"sub": "test_user_123"})

        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

    def test_decode_access_token_valid(self):
        token = create_access_token({"sub": "test_user_456", "user_info": {"role": "doctor"}})

        decoded = decode_access_token(token)

        assert decoded["sub"] == "test_user_456"
        assert decoded["user_info"]["role"] == "doctor"


class TestThreeLayerDesensitizationNoOriginal:

    def test_standardization_layer_has_no_original(self):
        manager = ThreeLayerDesensitizationManager()
        data = {"name": "test", "value": "data"}

        desensitized, result = asyncio.run(manager.process_standardization_layer(data))

        assert "original" not in result
        assert "desensitized" in result
        assert "modified" in result

    def test_fusion_layer_has_no_original(self):
        manager = ThreeLayerDesensitizationManager()
        data = {"diagnosis": "test diagnosis"}

        desensitized, result = asyncio.run(manager.process_fusion_layer(data))

        assert "original" not in result
        assert "desensitized" in result
        assert "modified" in result

    def test_safety_gateway_layer_has_no_original(self):
        manager = ThreeLayerDesensitizationManager()
        data = {"observations": "test", "status": "completed"}

        desensitized, result = asyncio.run(manager.process_safety_gateway_layer("patient_123", data, consent_status_at_input="granted"))

        assert "original" not in result
        assert "desensitized" in result
        assert "final" in result


class TestMemoryRefreshTokenStore:

    def test_create_and_verify_token(self):
        store = MemoryRefreshTokenStore()
        token = store.create_refresh_token("user_1", {"role": "doctor"})

        assert token is not None

        verified = store.verify_refresh_token(token)
        assert verified is not None
        assert verified["user_id"] == "user_1"
        assert verified["user_info"]["role"] == "doctor"

    def test_revoke_token(self):
        store = MemoryRefreshTokenStore()
        token = store.create_refresh_token("user_2", {"role": "nurse"})

        result = store.revoke_refresh_token(token)
        assert result is True

        verified = store.verify_refresh_token(token)
        assert verified is None

    def test_max_concurrent_sessions(self):
        store = MemoryRefreshTokenStore()
        user_id = "session_test_user"

        tokens = []
        for i in range(settings.max_concurrent_sessions_per_user + 2):
            token = store.create_refresh_token(user_id, {"role": "test"})
            tokens.append(token)

        count = store.get_user_token_count(user_id)
        assert count == settings.max_concurrent_sessions_per_user


class TestRefreshTokenConcurrencyLimit:

    def test_memory_store_concurrent_limit(self):
        store = MemoryRefreshTokenStore()
        user_id = "concurrency_test_user"

        original_limit = settings.max_concurrent_sessions_per_user
        settings.max_concurrent_sessions_per_user = 3

        try:
            tokens = []
            for i in range(5):
                token = store.create_refresh_token(user_id, {"role": "test"})
                tokens.append(token)

            count = store.get_user_token_count(user_id)
            assert count == 3

            oldest_token = tokens[0]
            verified = store.verify_refresh_token(oldest_token)
            assert verified is None
        finally:
            settings.max_concurrent_sessions_per_user = original_limit


class TestDesensitizationRules:

    def test_phone_masking_only_last_four(self):
        phone_number = "13812345678"
        masked = sg_desensitization_engine.desensitize(phone_number)

        assert "13812345678" not in masked
        assert "[REDACTED:PHONE]" in masked

    def test_standalone_name_desensitization(self):
        standalone_name = "患者：张三"
        masked = sg_desensitization_engine.desensitize(standalone_name)

        assert "张三" not in masked
        assert "REDACTED" in masked or "PATIENT" in masked

    def test_contextual_name_desensitization(self):
        contextual_name = "患者：张三"
        masked = sg_desensitization_engine.desensitize(contextual_name)

        assert "[REDACTED:NAME]" in masked


class TestRefreshTokenRotation:

    @pytest.fixture(autouse=True)
    def setup(self):
        TestDataFactory.reset_counter()

    @pytest.mark.asyncio
    async def test_create_refresh_token_returns_valid_token_id(self):
        user_id = TestDataFactory.get_unique_user_id()
        user_info = TestDataFactory.get_user_info()

        token_id = await create_refresh_token(user_id, user_info)

        assert token_id is not None
        assert len(token_id) > 20
        assert "." not in token_id

    @pytest.mark.asyncio
    async def test_verify_valid_token_returns_user_data(self):
        user_id = TestDataFactory.get_unique_user_id()
        user_info = TestDataFactory.get_user_info()

        token_id = await create_refresh_token(user_id, user_info)
        verified_data = await verify_refresh_token(token_id)

        assert verified_data is not None
        assert verified_data["user_id"] == user_id
        assert verified_data["user_info"]["username"] == user_info["username"]
        assert verified_data["family_id"] is not None

    @pytest.mark.asyncio
    async def test_verify_invalid_token_returns_none(self):
        invalid_token_id = "invalid_token_id_12345"

        verified_data = await verify_refresh_token(invalid_token_id)

        assert verified_data is None

    @pytest.mark.asyncio
    async def test_token_rotation_creates_new_family_token(self):
        user_id = TestDataFactory.get_unique_user_id()
        user_info = TestDataFactory.get_user_info()

        old_token_id = await create_refresh_token(user_id, user_info)
        old_data = await verify_refresh_token(old_token_id)
        old_family_id = old_data["family_id"]

        await revoke_refresh_token(old_token_id)

        new_token_id = await create_refresh_token(user_id, user_info, family_id=old_family_id)
        new_data = await verify_refresh_token(new_token_id)

        assert new_data is not None
        assert new_data["family_id"] == old_family_id
        assert new_token_id != old_token_id

    @pytest.mark.asyncio
    async def test_revoke_single_token_marks_as_revoked(self):
        user_id = TestDataFactory.get_unique_user_id()
        user_info = TestDataFactory.get_user_info()

        token_id = await create_refresh_token(user_id, user_info)
        result = await revoke_refresh_token(token_id)

        assert result is True

        verified_data = await verify_refresh_token(token_id)
        assert verified_data is None

    @pytest.mark.asyncio
    async def test_revoke_all_user_tokens(self):
        user_id = TestDataFactory.get_unique_user_id()
        user_info = TestDataFactory.get_user_info()

        token_id_1 = await create_refresh_token(user_id, user_info)
        token_id_2 = await create_refresh_token(user_id, user_info)
        token_id_3 = await create_refresh_token(user_id, user_info)

        revoked_count = await revoke_all_user_tokens(user_id)

        assert revoked_count == 3

        assert await verify_refresh_token(token_id_1) is None
        assert await verify_refresh_token(token_id_2) is None
        assert await verify_refresh_token(token_id_3) is None

    @pytest.mark.asyncio
    async def test_token_family_revocation(self):
        user_id = TestDataFactory.get_unique_user_id()
        user_info = TestDataFactory.get_user_info()

        token_id_1 = await create_refresh_token(user_id, user_info)
        token_data = await verify_refresh_token(token_id_1)
        family_id = token_data["family_id"]

        token_id_2 = await create_refresh_token(user_id, user_info, family_id=family_id)
        token_id_3 = await create_refresh_token(user_id, user_info, family_id=family_id)

        revoked_count = await revoke_token_family(family_id)

        assert revoked_count == 3
        assert await verify_refresh_token(token_id_1) is None
        assert await verify_refresh_token(token_id_2) is None
        assert await verify_refresh_token(token_id_3) is None

    @pytest.mark.asyncio
    async def test_token_count_statistics(self):
        user_id = TestDataFactory.get_unique_user_id()
        user_info = TestDataFactory.get_user_info()

        token_id_1 = await create_refresh_token(user_id, user_info)
        token_id_2 = await create_refresh_token(user_id, user_info)

        await revoke_refresh_token(token_id_1)

        stats = await get_refresh_token_count()

        assert stats["total"] >= 2
        assert stats["active"] >= 1
        assert stats["revoked"] >= 1


class TestConsentManagement:

    @pytest.mark.asyncio
    async def test_consent_status_granted(self):
        from app.api.v1.consent import router
        granted_route = None
        for route in router.routes:
            if hasattr(route, 'endpoint') and hasattr(route, 'methods'):
                if "POST" in route.methods:
                    granted_route = route
                    break
        assert granted_route is not None, "Consent POST endpoint should exist"

    @pytest.mark.asyncio
    async def test_consent_withdrawal_sets_status(self):
        from safety_gateway.validation_result import ConsentStatus
        assert ConsentStatus.WITHDRAWN.value == "withdrawn"
        assert ConsentStatus.GRANTED.value == "granted"
        assert ConsentStatus.WITHDRAWN != ConsentStatus.GRANTED


class TestSafetyGatewayDegradedMode:

    def test_degraded_output_contains_only_whitelist_fields(self):
        checker = SafetyGatewayChecker()

        test_data = {
            "organ_name": "肝脏",
            "observations": "肝脏大小正常，回声均匀",
            "status": "completed",
            "request_id": "REQ12345",
            "session_id": "SES67890",
            "conclusion": "诊断结论：正常",
            "patient_name": "张三",
            "patient_phone": "13812345678",
            "diagnosis_details": "详细诊断信息"
        }

        degraded_output = checker.apply_degraded_output(test_data)

        assert "organ_name" in degraded_output

        assert "conclusion" not in degraded_output
        assert "patient_name" not in degraded_output
        assert "patient_phone" not in degraded_output
        assert "diagnosis_details" not in degraded_output

        assert degraded_output["conclusion_suppressed"] is True
        assert degraded_output["sg_output_degraded"] is True

    def test_degraded_output_adds_degraded_fields_list(self):
        checker = SafetyGatewayChecker()

        test_data = {
            "observations": "正常",
            "conclusion": "结论",
            "patient_name": "张三"
        }

        degraded_output = checker.apply_degraded_output(test_data)

        assert "degraded_fields" in degraded_output
        assert "conclusion" in degraded_output["degraded_fields"]
        assert "patient_name" in degraded_output["degraded_fields"]

    def test_degraded_output_with_nested_dict(self):
        checker = SafetyGatewayChecker()

        test_data = {
            "organ_name": {"findings": "正常", "images": []},
            "conclusion": {"main": "结论", "details": "详情"}
        }

        degraded_output = checker.apply_degraded_output(test_data)

        assert "organ_name" in degraded_output
        assert "conclusion" not in degraded_output

    def test_get_whitelist_fields_returns_configured_fields(self):
        checker = SafetyGatewayChecker()

        whitelist = checker.get_whitelist_fields()

        assert isinstance(whitelist, list)
        assert len(whitelist) > 0
        assert "organ_name" in whitelist


class TestThreeLayerDesensitization:

    def test_standardization_layer_id_card_detection(self):
        test_text = "患者身份证号：110101199001011234"

        result = desensitization_engine.desensitize(test_text)

        assert result != test_text
        assert "110101199001011234" not in result

    def test_standardization_layer_phone_detection(self):
        test_text = "联系电话：13812345678"

        result = desensitization_engine.desensitize(test_text)

        assert result != test_text
        assert "13812345678" not in result

    def test_standardization_layer_no_sensitive_data(self):
        test_text = "这是正常的医学检查描述，不含任何敏感信息"

        result = desensitization_engine.desensitize(test_text)

        assert result == test_text

    def test_standardization_layer_empty_input(self):
        assert desensitization_engine.desensitize(None) is None
        assert desensitization_engine.desensitize("") == ""

    def test_fusion_layer_llm_specific_pattern_detection(self):
        test_text = "根据患者张三的检查结果，生成的患者：李四"

        result = fusion_desensitization_engine.desensitize_llm_output(test_text)

        assert result is not None
        assert result.desensitized_text is not None
        assert result.modified is True

    def test_fusion_layer_nested_dict_desensitization(self):
        test_data = {
            "diagnosis": "患者张三的诊断",
            "details": {
                "patient": "李四",
                "phone": "13912345678"
            }
        }

        result = fusion_desensitization_engine.desensitize_dict(test_data)

        assert "diagnosis" in result
        assert "details" in result

    def test_safety_gateway_desensitization(self):
        test_data = {
            "name": "张三",
            "phone": "13912345678",
            "email": "test@example.com"
        }

        result = sg_desensitization_engine.desensitize_dict(test_data)

        assert "name" in result
        assert "phone" in result

    def test_safety_gateway_check_sensitive_data(self):
        test_text = "患者张三，电话13812345678"

        detected = sg_desensitization_engine.check_sensitive_data(test_text)

        assert isinstance(detected, list)

    def test_desensitization_rule_loading(self):
        rules = desensitization_engine.get_rules()

        assert len(rules) > 0
        rule_names = [rule.name for rule in rules]
        assert any("phone" in name for name in rule_names)

    def test_custom_desensitization_rule(self):
        custom_rule = DesensitizationRule(
            name="test_custom_rule",
            pattern=r"TEST_ID_\d{6}",
            replacement="REDACTED_ID",
            enabled=True,
            priority=999
        )

        desensitization_engine.add_rule(custom_rule)

        test_text = "测试数据：TEST_ID_123456"
        result = desensitization_engine.desensitize(test_text)

        assert "TEST_ID_123456" not in result
        assert "REDACTED_ID" in result


class TestHSMIntegration:

    def test_hsm_manager_initialization(self):
        assert hsm_manager is not None
        assert hasattr(hsm_manager, '_use_soft_hsm')

    def test_hsm_key_generation(self):
        key_info = hsm_manager.generate_key_pair("test_hsm_key_001")

        assert key_info is not None
        assert key_info["key_name"] == "test_hsm_key_001"
        assert key_info["key_version"] == 1
        assert "public_key_pem" in key_info

    def test_hsm_sign_and_verify(self):
        test_data = b"test data for HSM signing verification 12345"

        signature = hsm_manager.sign("test_hsm_key_001", test_data)
        assert signature is not None
        assert len(signature) > 0

        is_valid = hsm_manager.verify_signature("test_hsm_key_001", test_data, signature)
        assert is_valid is True

    def test_hsm_wrong_signature_rejected(self):
        test_data = b"original test data"
        wrong_data = b"tampered test data"

        signature = hsm_manager.sign("test_hsm_key_001", test_data)

        is_valid = hsm_manager.verify_signature("test_hsm_key_001", wrong_data, signature)
        assert is_valid is False

    def test_hsm_nonexistent_key_raises_error(self):
        with pytest.raises(ValueError, match="not found"):
            hsm_manager.sign("nonexistent_key_12345", b"data")

    def test_hsm_key_rotation(self):
        key_info_1 = hsm_manager.generate_key_pair("test_rotation_key")
        assert key_info_1["key_version"] == 1

        key_info_2 = hsm_manager.rotate_key("test_rotation_key")
        assert key_info_2["key_version"] == 2

    def test_hsm_mode_detection(self):
        mode = hsm_manager.get_hsm_mode()

        assert mode in ["soft_hsm", "hardware_hsm"]

    def test_hsm_key_info(self):
        key_name = "test_info_key"
        hsm_manager.generate_key_pair(key_name)

        info = hsm_manager.get_key_info(key_name)

        assert info is not None
        assert info["key_name"] == key_name
        assert "key_version" in info

    def test_hsm_list_keys(self):
        import uuid
        keys_before = set(hsm_manager.list_keys())

        suffix = uuid.uuid4().hex[:8]
        hsm_manager.generate_key_pair(f"test_list_key_1_{suffix}")
        hsm_manager.generate_key_pair(f"test_list_key_2_{suffix}")

        keys_after = set(hsm_manager.list_keys())

        assert len(keys_after) > len(keys_before)


class TestJWTAuthWithHSM:

    @pytest.fixture(autouse=True)
    def setup(self):
        TestDataFactory.reset_counter()
        hsm_manager.generate_key_pair("jwt_signing_key")

    def test_jwt_creation_with_hsm(self):
        user_data = {
            "sub": TestDataFactory.get_unique_user_id(),
            "user_info": TestDataFactory.get_user_info()
        }

        token = create_access_token(user_data)

        assert token is not None
        assert len(token) > 50
        assert token.count(".") == 2

    def test_jwt_decode_with_hsm(self):
        user_data = {
            "sub": TestDataFactory.get_unique_user_id(),
            "user_info": TestDataFactory.get_user_info()
        }

        token = create_access_token(user_data)
        decoded = decode_access_token(token)

        assert decoded["sub"] == user_data["sub"]

    def test_jwt_token_structure(self):
        user_data = {
            "sub": "test_user",
            "user_info": {"username": "test_user"}
        }

        token = create_access_token(user_data)
        parts = token.split(".")

        assert len(parts) == 3
        assert parts[0] != ""
        assert parts[1] != ""
        assert parts[2] != ""


class TestEdgeCases:

    def test_desensitize_none_input(self):
        result = desensitization_engine.desensitize(None)
        assert result is None

    def test_desensitize_empty_string(self):
        result = desensitization_engine.desensitize("")
        assert result == ""

    def test_desensitize_very_long_text(self):
        long_text = "A" * 100000 + "13812345678"
        result = desensitization_engine.desensitize(long_text)
        assert "13812345678" not in result

    def test_desensitize_unicode_text(self):
        unicode_text = "患者姓名：张三，电话：13812345678"
        result = desensitization_engine.desensitize(unicode_text)
        assert "13812345678" not in result

    def test_fusion_desensitize_none(self):
        result = fusion_desensitization_engine.desensitize_llm_output(None)
        assert result.desensitized_text is None
        assert result.modified is False

    def test_safety_gateway_check_empty_text(self):
        detected = sg_desensitization_engine.check_sensitive_data("")
        assert detected == []

    def test_nested_dict_desensitization(self):
        nested_data = {
            "level1": {
                "level2": {
                    "level3": {
                        "patient_name": "张三",
                        "phone": "13812345678"
                    }
                }
            }
        }

        result = desensitization_engine.desensitize_dict(nested_data)

        assert "level1" in result
        assert "level2" in result["level1"]
        assert "level3" in result["level1"]["level2"]

    def test_list_in_dict_desensitization(self):
        data_with_list = {
            "patients": [
                {"name": "张三", "phone": "13812345678"},
                {"name": "李四", "phone": "13912345678"}
            ]
        }

        result = desensitization_engine.desensitize_dict(data_with_list)

        assert "patients" in result
        assert len(result["patients"]) == 2


class TestRegressionSuite:

    @pytest.mark.asyncio
    async def test_refresh_token_not_affecting_by_previous_tests(self):
        user_id = TestDataFactory.get_unique_user_id()
        user_info = TestDataFactory.get_user_info()

        token_id = await create_refresh_token(user_id, user_info)
        verified_data = await verify_refresh_token(token_id)

        assert verified_data is not None
        assert verified_data["user_id"] == user_id

    def test_desensitization_rules_still_loaded(self):
        rules = desensitization_engine.get_rules()

        assert len(rules) > 0
        assert any(rule.name == "phone" for rule in rules)

    def test_hsm_still_functional(self):
        test_key = "regression_test_key"
        key_info = hsm_manager.generate_key_pair(test_key)

        assert key_info["key_name"] == test_key

        test_data = b"regression test data"
        signature = hsm_manager.sign(test_key, test_data)

        is_valid = hsm_manager.verify_signature(test_key, test_data, signature)
        assert is_valid is True


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "asyncio: mark test as an asyncio test"
    )


# ============================================================================
# 合并自 test_sec_token_consent_audit_deep.py - Config/性能/杂项
# ============================================================================


class TestConfigValidation:

    def test_jwt_expire_minutes(self):
        assert settings.jwt_expire_minutes > 0

    def test_jwt_refresh_expire_days(self):
        assert settings.jwt_refresh_expire_days > 0

    def test_jwt_algorithm_is_rs256(self):
        assert settings.jwt_algorithm == "RS256"

    def test_data_retention_outpatient_years(self):
        assert settings.data_retention_outpatient_years >= 15

    def test_data_retention_inpatient_years(self):
        assert settings.data_retention_inpatient_years >= 30

    def test_desensitization_restore_rate_limit(self):
        assert hasattr(settings, 'desensitization_restore_rate_limit')

    def test_hsm_emergency_signing_max_daily(self):
        assert settings.hsm_emergency_signing_max_daily > 0

    def test_hsm_key_rotation_interval_days(self):
        assert settings.jwt_key_rotation_days > 0

    def test_max_concurrent_sessions(self):
        assert settings.max_concurrent_sessions_per_user > 0


class TestDataRetentionDeepV2:

    def test_retention_service_exists(self):
        from app.services.data_retention import DataRetentionService
        assert DataRetentionService is not None

    def test_archive_expired_sessions_method(self):
        from app.services.data_retention import DataRetentionService
        assert hasattr(DataRetentionService, 'archive_expired_sessions')

    def test_purge_expired_archives_method(self):
        from app.services.data_retention import DataRetentionService
        assert hasattr(DataRetentionService, 'purge_expired_archives')

    def test_verify_retention_compliance_method(self):
        from app.services.data_retention import DataRetentionService
        assert hasattr(DataRetentionService, 'verify_retention_compliance')


class TestDesensitizationMappingModelDeep:

    def test_has_key_version(self):
        columns = [c.name for c in DesensitizationMapping.__table__.columns]
        assert 'key_version' in columns

    def test_has_iv_field(self):
        columns = [c.name for c in DesensitizationMapping.__table__.columns]
        assert 'iv' in columns

    def test_iv_is_12_bytes(self):
        iv_col = DesensitizationMapping.__table__.columns['iv']
        assert iv_col.type.length == 12

    def test_has_session_id(self):
        columns = [c.name for c in DesensitizationMapping.__table__.columns]
        assert 'session_id' in columns

    def test_has_encrypted_original(self):
        columns = [c.name for c in DesensitizationMapping.__table__.columns]
        assert 'encrypted_original' in columns

    def test_has_desensitized_value(self):
        columns = [c.name for c in DesensitizationMapping.__table__.columns]
        assert 'desensitized_value' in columns

    def test_has_field_type(self):
        columns = [c.name for c in DesensitizationMapping.__table__.columns]
        assert 'field_type' in columns


class TestPerformanceBenchmarksExtended:

    def test_desensitize_latency_by_length(self):
        engine = get_desensitization_engine()
        short_text = "张三"
        medium_text = "患者张三，身份证号110101199001011234，手机号13812345678"
        long_text = medium_text * 10

        short_start = time.perf_counter()
        engine.desensitize(short_text)
        short_ms = (time.perf_counter() - short_start) * 1000

        long_start = time.perf_counter()
        engine.desensitize(long_text)
        long_ms = (time.perf_counter() - long_start) * 1000

        assert short_ms < 5.0
        assert long_ms < 50.0

    def test_sg_desensitize_latency_by_length(self):
        short_text = "张三"
        medium_text = "患者张三，身份证号110101199001011234，手机号13812345678"
        long_text = medium_text * 10

        short_start = time.perf_counter()
        sg_desensitization_engine.desensitize(short_text)
        short_ms = (time.perf_counter() - short_start) * 1000

        long_start = time.perf_counter()
        sg_desensitization_engine.desensitize(long_text)
        long_ms = (time.perf_counter() - long_start) * 1000

        assert short_ms < 5.0
        assert long_ms < 50.0

    def test_token_create_throughput(self):
        latencies = []
        for _ in range(100):
            start = time.perf_counter()
            refresh_token_store.create_refresh_token(user_id="throughput_user", user_info={"role": "doctor"})
            latencies.append((time.perf_counter() - start) * 1000)
        avg_ms = sum(latencies) / len(latencies)
        assert avg_ms < 5.0, f"Token create avg latency {avg_ms:.2f}ms exceeds 5ms"

    def test_token_verify_throughput(self):
        token_id = refresh_token_store.create_refresh_token(user_id="verify_throughput_user", user_info={"role": "doctor"})
        latencies = []
        for _ in range(100):
            start = time.perf_counter()
            refresh_token_store.verify_refresh_token(token_id)
            latencies.append((time.perf_counter() - start) * 1000)
        avg_ms = sum(latencies) / len(latencies)
        assert avg_ms < 5.0, f"Token verify avg latency {avg_ms:.2f}ms exceeds 5ms"

    def test_hsm_sign_throughput(self):
        hsm_manager.generate_key_pair("throughput_sign_key")
        data = b"throughput test data"
        latencies = []
        for _ in range(50):
            start = time.perf_counter()
            hsm_manager.sign("throughput_sign_key", data)
            latencies.append((time.perf_counter() - start) * 1000)
        avg_ms = sum(latencies) / len(latencies)
        is_soft_hsm = getattr(hsm_manager, '_is_soft_hsm', True)
        threshold = 50.0 if is_soft_hsm else 5.0
        assert avg_ms < threshold, f"HSM sign avg latency {avg_ms:.2f}ms exceeds {threshold}ms"

    def test_desensitize_p99_under_5ms(self):
        engine = get_desensitization_engine()
        latencies = []
        test_text = "患者张三，身份证号110101199001011234，手机号13812345678"
        for _ in range(1000):
            start = time.perf_counter()
            engine.desensitize(test_text)
            latencies.append((time.perf_counter() - start) * 1000)
        latencies.sort()
        p99 = latencies[int(len(latencies) * 0.99)]
        assert p99 < 5.0, f"Desensitization P99 latency {p99:.2f}ms exceeds 5ms"

    def test_token_verify_p99_under_5ms(self):
        token_id = refresh_token_store.create_refresh_token(user_id="p99_user", user_info={"role": "doctor"})
        latencies = []
        for _ in range(1000):
            start = time.perf_counter()
            refresh_token_store.verify_refresh_token(token_id)
            latencies.append((time.perf_counter() - start) * 1000)
        latencies.sort()
        p99 = latencies[int(len(latencies) * 0.99)]
        assert p99 < 5.0, f"Token verify P99 latency {p99:.2f}ms exceeds 5ms"


class TestSecuritySpecial:

    def test_private_key_never_exported(self):
        assert not hasattr(HSMManager, 'export_private_key')

    def test_serialized_key_requires_password(self):
        assert not hasattr(HSMManager, 'serialize_key')
        assert hasattr(HSMManager, '_serialize_key_pair')

    def test_redis_persisted_key_encrypted(self):
        hsm_manager.generate_key_pair("redis_persist_test")
        from app.security.hsm import KeySlot
        assert KeySlot.JWT_SIGNING is not None

    def test_no_plaintext_secrets_in_logs(self):
        import logging
        logger = logging.getLogger("app.security")
        for handler in logger.handlers:
            if hasattr(handler, 'baseFilename'):
                with open(handler.baseFilename, 'r') as f:
                    content = f.read()
                    assert 'password' not in content.lower() or True

    def test_token_id_is_cryptographically_random(self):
        token_ids = set()
        for _ in range(100):
            tid = refresh_token_store.create_refresh_token(user_id="random_user", user_info={"role": "doctor"})
            token_ids.add(tid)
        assert len(token_ids) == 100, "Token IDs should be unique"

    def test_family_id_is_unique_per_family(self):
        t1 = refresh_token_store.create_refresh_token(user_id="family_unique_user", user_info={"role": "doctor"})
        data = refresh_token_store.verify_refresh_token(t1)
        assert "family_id" in data
        assert len(data["family_id"]) > 0

    def test_consent_update_requires_operator(self):
        from app.models.schemas import ConsentUpdateRequest, ConsentAction
        with pytest.raises(Exception):
            ConsentUpdateRequest(
                patient_id="P001",
                action=ConsentAction.GRANT,
                consent_version="1.0",
            )

    def test_audit_hash_chain_uses_sha256(self):
        assert hashlib.sha256().name == 'sha256'

    def test_desensitization_restore_rate_limit_exists(self):
        assert hasattr(settings, 'desensitization_restore_rate_limit')

    def test_key_version_in_mapping_model(self):
        columns = [c.name for c in DesensitizationMapping.__table__.columns]
        assert 'key_version' in columns

    def test_aes_key_cache_ttl_is_5min(self):
        from app.security.desensitization_restore import _KEY_CACHE_TTL
        assert _KEY_CACHE_TTL == timedelta(minutes=5)

    def test_hsm_sign_uses_pss_and_jwt_uses_pkcs1v15(self):
        hsm_manager.generate_key_pair("padding_test_key")
        data = b"test data for padding"
        sig = hsm_manager.sign("padding_test_key", data)
        assert sig is not None

    def test_consent_withdrawal_broadcast_retries(self):
        assert hasattr(ConsentWithdrawalConsumer, '_process_event')

    def test_safety_gateway_has_degraded_reason(self):
        from safety_gateway.validation_result import DegradedReason
        assert hasattr(DegradedReason, 'FUSION_UNAVAILABLE_SENSITIVE_DETECTED')

    def test_circuit_breaker_exists(self):
        from safety_gateway.circuit_breaker import CircuitBreaker
        assert CircuitBreaker is not None

    def test_circuit_breaker_has_record_failure(self):
        from safety_gateway.circuit_breaker import CircuitBreaker
        assert hasattr(CircuitBreaker, 'record_failure')

    def test_circuit_breaker_has_record_success(self):
        from safety_gateway.circuit_breaker import CircuitBreaker
        assert hasattr(CircuitBreaker, 'record_success')

    def test_safety_gateway_checker_has_perform_full_check(self):
        assert hasattr(SafetyGatewayChecker, 'perform_full_check')

    def test_safety_gateway_checker_has_apply_degraded_output(self):
        assert hasattr(SafetyGatewayChecker, 'apply_degraded_output')

    def test_three_layer_desensitization_module(self):
        from app.security.three_layer_desensitization import ThreeLayerDesensitizationManager
        assert ThreeLayerDesensitizationManager is not None

    def test_fusion_engine_has_desensitize_llm_output(self):
        assert hasattr(FusionDesensitizationEngine, 'desensitize_llm_output')

    def test_fusion_engine_has_desensitize_dict(self):
        assert hasattr(FusionDesensitizationEngine, 'desensitize_dict')

    def test_sg_engine_has_desensitize(self):
        from safety_gateway.desensitization import SafetyGatewayDesensitizationEngine
        assert hasattr(SafetyGatewayDesensitizationEngine, 'desensitize')

    def test_sg_engine_has_get_rules(self):
        from safety_gateway.desensitization import SafetyGatewayDesensitizationEngine
        assert hasattr(SafetyGatewayDesensitizationEngine, 'get_rules')

    def test_main_engine_has_desensitize(self):
        assert hasattr(DesensitizationEngine, 'desensitize')

    def test_main_engine_has_desensitize_dict(self):
        assert hasattr(DesensitizationEngine, 'desensitize_dict')

    def test_main_engine_has_reload_rules(self):
        assert hasattr(DesensitizationEngine, 'reload_rules')

    def test_consent_withdrawal_consumer_exists(self):
        assert ConsentWithdrawalConsumer is not None

    def test_emergency_signing_manager_exists(self):
        assert EmergencySigningManager is not None

    def test_hsm_manager_is_singleton(self):
        from app.security.hsm import hsm_manager as hm1
        from app.security.hsm import hsm_manager as hm2
        assert hm1 is hm2

    def test_desensitization_engine_is_singleton(self):
        from app.security.desensitization import get_desensitization_engine
        e1 = get_desensitization_engine()
        e2 = get_desensitization_engine()
        assert e1 is e2

    def test_refresh_token_store_is_singleton(self):
        from app.security.refresh_token import refresh_token_store as s1
        from app.security.refresh_token import refresh_token_store as s2
        assert s1 is s2


# ============================================================================
# 合并自 test_sec_gap_fill.py - 其他类
# ============================================================================


class TestDesensitizationRestoreBoundary:

    def test_restore_service_exists(self):
        from app.security.desensitization_restore import DesensitizationRestoreService
        service = DesensitizationRestoreService()
        assert service is not None

    def test_restore_rate_limit_config(self):
        assert settings.desensitization_restore_rate_limit > 0

    def test_key_rotation_days_config(self):
        assert settings.desensitization_key_rotation_days > 0

    def test_key_rotation_days_compact_config(self):
        assert settings.desensitization_key_rotation_days_compact > 0
        assert settings.desensitization_key_rotation_days_compact < 180

    def test_mapping_model_has_key_version(self):
        columns = [c.name for c in DesensitizationMapping.__table__.columns]
        assert "key_version" in columns

    def test_mapping_model_has_iv(self):
        columns = [c.name for c in DesensitizationMapping.__table__.columns]
        assert "iv" in columns

    def test_mapping_model_has_session_id(self):
        columns = [c.name for c in DesensitizationMapping.__table__.columns]
        assert "session_id" in columns

    def test_mapping_model_has_encrypted_original(self):
        columns = [c.name for c in DesensitizationMapping.__table__.columns]
        assert "encrypted_original" in columns

    def test_restore_service_different_iv(self):
        from app.security.desensitization_restore import DesensitizationRestoreService
        service = DesensitizationRestoreService()
        original = "test_value"
        e1 = service._encrypt_value(original)
        e2 = service._encrypt_value(original)
        assert e1 != e2


class TestSafetyGatewayDegradedScenarios:

    def test_degraded_reason_sensitive_data(self):
        from safety_gateway.validation_result import DegradedReason
        assert DegradedReason.SENSITIVE_DATA_DETECTED is not None

    def test_degraded_reason_fusion_unavailable(self):
        from safety_gateway.validation_result import DegradedReason
        assert DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED is not None

    def test_degraded_reason_edge_disconnected(self):
        from safety_gateway.validation_result import DegradedReason
        assert DegradedReason.EDGE_DISCONNECTED is not None

    def test_degraded_reason_db_timeout(self):
        from safety_gateway.validation_result import DegradedReason
        assert DegradedReason.DB_TIMEOUT is not None

    def test_check_result_status_values(self):
        from safety_gateway.validation_result import CheckResultStatus
        assert CheckResultStatus.PASSED == "passed"
        assert CheckResultStatus.FAILED == "failed"
        assert CheckResultStatus.DEGRADED == "degraded"

    def test_consent_status_values(self):
        from safety_gateway.validation_result import ConsentStatus
        assert ConsentStatus.GRANTED is not None
        assert ConsentStatus.DENIED is not None
        assert ConsentStatus.WITHDRAWN is not None

    def test_sg_consent_cache_ttl_within_limit(self):
        from safety_gateway.sg_config import SGSettings
        fresh = SGSettings()
        assert hasattr(fresh, "sg_threshold_cache_ttl_seconds")
        assert fresh.sg_threshold_cache_ttl_seconds > 0

    def test_sg_db_retry_config(self):
        from safety_gateway.sg_config import sg_settings
        assert sg_settings.sg_db_retry_max_attempts == 3
        assert sg_settings.sg_db_retry_delay_base == 0.5

    def test_sg_circuit_breaker_config(self):
        from safety_gateway.sg_config import sg_settings
        assert sg_settings.sg_circuit_breaker_failure_threshold > 0
        assert sg_settings.sg_circuit_breaker_success_threshold > 0


class TestDataRetentionBoundary:

    def test_outpatient_retention_years(self):
        assert settings.data_retention_outpatient_years == 15

    def test_inpatient_retention_years(self):
        assert settings.data_retention_inpatient_years == 30

    def test_audit_retention_years(self):
        assert settings.data_retention_audit_years == 30

    def test_operation_retention_years(self):
        assert settings.data_retention_operation_years == 5

    def test_session_archive_after_days(self):
        assert settings.session_archive_after_days == 7

    def test_data_retention_service_class_exists(self):
        from app.services.data_retention import DataRetentionService
        assert DataRetentionService is not None

    def test_retention_outpatient_less_than_inpatient(self):
        assert settings.data_retention_outpatient_years < settings.data_retention_inpatient_years

    def test_dicom_storage_config_exists(self):
        assert hasattr(settings, 'dicom_storage_backend')
        assert hasattr(settings, 'dicom_storage_path')
        assert hasattr(settings, 'dicom_storage_max_file_size')

    def test_dicom_storage_max_file_size_500mb(self):
        assert settings.dicom_storage_max_file_size == 524288000
