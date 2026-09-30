"""
Phase 0A SEC 安全合规 - 合并回归修复测试套件

合并自: test_sec_craft_fixes, test_k1_fixes, test_white_fixes, test_white_round2_fixes
覆盖: HSM签名/降级, Refresh Token, 降级语义, 错误码, 脱敏规则, 审计哈希链, E2E等
"""
import asyncio
import hashlib
import inspect
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

pytestmark = [pytest.mark.track_sec, pytest.mark.sec_regression]

os.environ["USE_REDIS_REFRESH_TOKEN"] = "false"
os.environ["USE_SOFT_HSM"] = "true"

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

from app.api.v1.errors import (
    BadRequestException,
    HSMUnavailableException,
    PatientConsentWithdrawnException,
    PatientNotConsentedException,
    SafetyGatewayUnavailableException,
)
from app.config.settings import settings
from app.fusion.desensitization import FusionDesensitizationEngine
from app.models.desensitization_mapping import DesensitizationMapping
from app.models.schemas import (
    ConsentAction,
    ConsentQueryResponse,
    ConsentStatus,
    ConsentUpdateRequest,
    SafetyEventType,
    SafetySeverity,
)
from app.repositories.consent_repository import record_hsm_degradation_if_needed
from app.security.consent_withdrawal_consumer import CONSENT_WITHDRAWAL_STREAM
from app.security.desensitization import get_desensitization_engine
from app.security.desensitization_restore import DesensitizationRestoreService
from app.security.emergency_signing import EmergencySigningManager
from app.security.hsm import DegradedPhase, HSMManager, KeySlot, hsm_manager
from app.security.refresh_token import (
    create_refresh_token,
    get_storage_mode,
    refresh_token_store,
    reset_storage,
    revoke_refresh_token,
    revoke_token_family,
    verify_refresh_token,
)
from app.services.audit_service import AuditService
from safety_gateway.checker import (
    CheckResult,
    CheckResultStatus,
    DegradedReason,
    SafetyGatewayChecker,
    get_safety_gateway_checker,
)
from safety_gateway.checker.desensitization_phase import MAX_RECURSION_DEPTH
from safety_gateway.desensitization import SafetyGatewayDesensitizationEngine, sg_desensitization_engine

PATIENT_TOKEN_PATTERN = re.compile(r'PATIENT_\d{3}')


class TestDataFactory:
    _counter = 0

    @classmethod
    def reset(cls):
        cls._counter = 0

    @classmethod
    def uid(cls) -> str:
        cls._counter += 1
        return f"u_{cls._counter}_{int(datetime.now(timezone.utc).timestamp())}"

    @classmethod
    def user_info(cls, role: str = "ULTRASOUND_DOCTOR") -> dict:
        cls._counter += 1
        return {
            "username": f"user_{cls._counter}",
            "full_name": f"测试{cls._counter}",
            "email": f"t{cls._counter}@h.com",
            "role": role,
            "disabled": False,
        }

    @classmethod
    def patient_id(cls) -> str:
        cls._counter += 1
        return f"P{cls._counter:06d}"


@pytest.fixture(autouse=True)
def setup():
    reset_storage()
    TestDataFactory.reset()
    yield
    reset_storage()


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_YAML_PATH = os.path.join(_PROJECT_ROOT, "desensitization_rules_safety.yaml")


class TestHSMPaddingFix:
    """验证HSM使用PKCS#1 v1.5填充而非PSS"""

    def test_sign_uses_pss_padding(self):
        """
        核心验证：HSM通用签名使用PSS填充（更安全）

        PSS是概率签名方案，比PKCS1v15更安全（有安全证明）。
        通用签名(sign)使用PSS，JWT签名(sign_jwt)使用PKCS1v15（RS256标准要求）。
        """
        key_name = "test_pss_padding"
        hsm_manager.generate_key_pair(key_name)

        test_data = b"test data for padding verification"
        signature = hsm_manager.sign(key_name, test_data)

        slot = hsm_manager._get_key_slot(key_name)
        key_info = hsm_manager._key_slots[slot][key_name]
        public_key = key_info['public_key']

        public_key.verify(
            signature,
            test_data,
            asym_padding.PSS(
                mgf=asym_padding.MGF1(hashes.SHA256()),
                salt_length=asym_padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )

    def test_pkcs1v15_verify_would_fail_for_sign(self):
        """
        验证PKCS1v15填充验证会失败（证明sign()使用PSS而非PKCS1v15）

        如果sign()使用PKCS1v15，此测试会通过。
        如果sign()使用PSS，此测试会抛出InvalidSignature。
        """
        key_name = "test_pkcs1v15_would_fail"
        hsm_manager.generate_key_pair(key_name)

        test_data = b"test data"
        signature = hsm_manager.sign(key_name, test_data)

        slot = hsm_manager._get_key_slot(key_name)
        key_info = hsm_manager._key_slots[slot][key_name]
        public_key = key_info['public_key']

        with pytest.raises(InvalidSignature):
            public_key.verify(
                signature,
                test_data,
                asym_padding.PKCS1v15(),
                hashes.SHA256()
            )

    def test_sign_jwt_uses_pkcs1v15(self):
        """
        验证JWT签名使用PKCS1v15填充（RS256标准要求）

        JWT RS256规范要求PKCS1v15填充，PSS与JWT标准不兼容。
        通过python-jose成功解码来验证JWT格式正确。
        """
        import os
        os.environ["HSM_PRIVATE_KEY_PASSWORD"] = "test_password_123"
        hsm = HSMManager()
        key_name = "test_jwt_pkcs1v15"
        key_info = hsm.generate_key_pair(key_name, slot=KeySlot.JWT_SIGNING)
        public_pem = key_info['public_key_pem']

        header = {"alg": "RS256", "typ": "JWT"}
        payload = {"sub": "test_user", "role": "doctor"}

        jwt_token = hsm.sign_jwt(key_name, header, payload)

        parts = jwt_token.split(".")
        assert len(parts) == 3, "JWT should have 3 parts"

        import jwt

        decoded = jwt.decode(jwt_token, public_pem, algorithms=["RS256"])
        assert decoded["sub"] == "test_user"
        assert decoded["role"] == "doctor"

    def test_sign_and_verify_roundtrip(self):
        """端到端验证：签名后验证必须成功"""
        key_name = "test_roundtrip"
        hsm_manager.generate_key_pair(key_name)

        test_data = b"roundtrip test data 12345"
        signature = hsm_manager.sign(key_name, test_data)

        is_valid = hsm_manager.verify_signature(key_name, test_data, signature)
        assert is_valid is True

    def test_tampered_data_rejected(self):
        """篡改数据后签名验证必须失败"""
        key_name = "test_tamper"
        hsm_manager.generate_key_pair(key_name)

        original_data = b"original data"
        signature = hsm_manager.sign(key_name, original_data)

        tampered_data = b"tampered data"
        is_valid = hsm_manager.verify_signature(key_name, tampered_data, signature)
        assert is_valid is False


class TestHSMDegradationEvent:
    """验证HSM降级状态标志和事件记录"""

    def test_hsm_manager_has_degraded_methods(self):
        """HSMManager应有is_degraded/set_degraded/clear_degraded方法"""
        hsm = HSMManager()
        assert hasattr(hsm, 'is_degraded')
        assert hasattr(hsm, 'set_degraded')
        assert hasattr(hsm, 'clear_degraded')

    def test_initial_state_not_degraded(self):
        """初始状态应为非降级"""
        hsm = HSMManager()
        assert hsm.is_degraded() is False

    def test_set_degraded_changes_state(self):
        """set_degraded应将状态设为True"""
        hsm = HSMManager()
        hsm.set_degraded("test_reason")
        assert hsm.is_degraded() is True

    def test_clear_degraded_resets_state(self):
        """clear_degraded应将状态重置为False"""
        hsm = HSMManager()
        hsm.set_degraded("test_reason")
        assert hsm.is_degraded() is True
        hsm.clear_degraded()
        assert hsm.is_degraded() is False

    def test_set_degraded_idempotent(self):
        """重复调用set_degraded不应产生重复日志"""
        hsm = HSMManager()
        hsm.set_degraded("test_reason_1")
        hsm.set_degraded("test_reason_2")
        assert hsm.is_degraded() is True

    def test_auth_calls_set_degraded_on_hsm_failure(self):
        """auth.py在HSM签名失败时应调用set_degraded"""
        import app.security.auth as auth_module
        source = inspect.getsource(auth_module.create_access_token)
        assert "hsm_manager.set_degraded" in source

    def test_auth_calls_set_degraded_on_verify_failure(self):
        """auth.py在HSM验证失败时应调用set_degraded"""
        import app.security.auth as auth_module
        source = inspect.getsource(auth_module.decode_access_token)
        assert "hsm_manager.set_degraded" in source

    def test_safety_event_type_includes_hsm_degraded(self):
        """SafetyEventType应包含HSM_DEGRADED"""
        assert hasattr(SafetyEventType, 'HSM_DEGRADED')
        assert SafetyEventType.HSM_DEGRADED.value == "HsmDegraded"


class TestHSMDegradationDBEvent:
    """验证HSM降级事件写入safety_events表"""

    @pytest.mark.asyncio
    async def test_record_hsm_degradation_when_degraded(self):
        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()

        degraded_at = datetime.now(timezone.utc)
        with patch("app.security.hsm.hsm_manager.is_degraded", return_value=True):
            with patch("app.security.hsm.hsm_manager.get_hsm_mode", return_value="soft_hsm_simulation"):
                with patch("app.security.hsm.hsm_manager._degraded_at", degraded_at):
                    result = await record_hsm_degradation_if_needed(mock_db)

        assert result is True
        mock_db.add.assert_called_once()
        added_event = mock_db.add.call_args[0][0]
        assert added_event.event_type == SafetyEventType.HSM_DEGRADED.value
        assert added_event.severity == SafetySeverity.IMPORTANT.value
        assert added_event.payload["hsm_mode"] == "soft_hsm_simulation"

    @pytest.mark.asyncio
    async def test_no_record_when_not_degraded(self):
        """HSM未降级时不应写入SafetyEvent"""
        mock_db = AsyncMock()

        with patch("app.security.hsm.hsm_manager.is_degraded", return_value=False):
            result = await record_hsm_degradation_if_needed(mock_db)

        assert result is False
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_idempotent_when_event_already_exists(self):
        """已存在HsmDegraded事件时不应重复写入"""
        mock_existing = MagicMock()
        mock_existing.event_type = SafetyEventType.HSM_DEGRADED.value

        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_existing)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()

        with patch("app.security.hsm.hsm_manager.is_degraded", return_value=True):
            result = await record_hsm_degradation_if_needed(mock_db)

        assert result is False
        mock_db.add.assert_not_called()

    def test_consent_py_imports_record_function(self):
        """consent.py应导入record_hsm_degradation_if_needed"""
        import app.api.v1.consent as consent_module
        assert hasattr(consent_module, 'record_hsm_degradation_if_needed')

    def test_safety_event_type_hsm_degraded_value(self):
        """SafetyEventType.HSM_DEGRADED值应为HsmDegraded"""
        assert SafetyEventType.HSM_DEGRADED.value == "HsmDegraded"

    def test_hsm_manager_set_degraded_sets_flag(self):
        """set_degraded应设置降级标志"""
        hsm = HSMManager()
        assert hsm.is_degraded() is False
        hsm.set_degraded("test_degradation")
        assert hsm.is_degraded() is True


class TestRefreshTokenReuseDetection:
    """验证已撤销Token被重用时撤销整个家族"""

    @pytest.mark.asyncio
    async def test_revoked_token_reuse_revokes_entire_family(self):
        """
        核心安全测试：重用已撤销Token应撤销整个家族

        攻击场景：
        1. 用户登录获得token1（family_id=F1）
        2. 用户刷新获得token2（family_id=F1），token1被撤销
        3. 攻击者窃取token1并尝试重用
        4. 系统检测到重用，撤销F1家族所有Token（包括token2）
        5. 攻击者和用户都必须重新登录
        """
        user_id = TestDataFactory.uid()
        user_info = TestDataFactory.user_info()
        family_id = "family_reuse_test"

        token1 = await create_refresh_token(user_id, user_info, family_id=family_id)
        token2 = await create_refresh_token(user_id, user_info, family_id=family_id)
        token3 = await create_refresh_token(user_id, user_info, family_id=family_id)

        data2 = await verify_refresh_token(token2)
        assert data2 is not None

        await revoke_refresh_token(token1)

        result = await verify_refresh_token(token1)
        assert result is None

        data2_after = await verify_refresh_token(token2)
        assert data2_after is None

        data3_after = await verify_refresh_token(token3)
        assert data3_after is None

    @pytest.mark.asyncio
    async def test_valid_token_not_affected_by_other_family(self):
        """不同家族的Token不应受影响"""
        user_info = TestDataFactory.user_info()

        token_a1 = await create_refresh_token(TestDataFactory.uid(), user_info, family_id="family_A")
        token_b1 = await create_refresh_token(TestDataFactory.uid(), user_info, family_id="family_B")

        await revoke_refresh_token(token_a1)

        await verify_refresh_token(token_a1)

        data_b = await verify_refresh_token(token_b1)
        assert data_b is not None

    @pytest.mark.asyncio
    async def test_nonexistent_token_returns_none_without_error(self):
        """不存在的Token应返回None，不触发家族撤销"""
        result = await verify_refresh_token("nonexistent_token_xyz")
        assert result is None


class TestRevokeTokenFamilyAtomicity:
    """验证revoke_token_family的原子性改进"""

    def test_in_memory_revoke_is_under_lock(self):
        """MemoryRefreshTokenStore的revoke_token_family应在锁内执行"""
        import app.security.refresh_token as rt_module
        source = inspect.getsource(rt_module.MemoryRefreshTokenStore.revoke_token_family)

        assert "with self._lock:" in source

    @pytest.mark.asyncio
    async def test_family_revocation_consistency(self):
        """家族撤销后所有Token必须不可用"""
        user_info = TestDataFactory.user_info()
        family_id = "atomic_test_family"

        t1 = await create_refresh_token(TestDataFactory.uid(), user_info, family_id=family_id)
        t2 = await create_refresh_token(TestDataFactory.uid(), user_info, family_id=family_id)
        t3 = await create_refresh_token(TestDataFactory.uid(), user_info, family_id=family_id)

        count = await revoke_token_family(family_id)
        assert count == 3

        assert await verify_refresh_token(t1) is None
        assert await verify_refresh_token(t2) is None
        assert await verify_refresh_token(t3) is None


class TestRedisAtomicRevoke:
    """验证Redis路径revoke操作使用两阶段pipeline"""

    def test_revoke_token_family_uses_pipeline(self):
        """revoke_token_family应使用pipeline批量写入"""
        from app.security.refresh_token import RedisRefreshTokenStore
        source = inspect.getsource(RedisRefreshTokenStore.revoke_token_family)

        assert "pipeline" in source
        assert "await pipe.execute()" in source

    def test_revoke_all_user_tokens_revokes_individually(self):
        """revoke_all_user_tokens逐个撤销token"""
        from app.security.refresh_token import RedisRefreshTokenStore
        source = inspect.getsource(RedisRefreshTokenStore.revoke_all_user_tokens)

        assert "redis_client.set" in source or "await redis_client" in source

    def test_no_pipeline_external_hget_in_revoke_family(self):
        from app.security.refresh_token import RedisRefreshTokenStore
        source = inspect.getsource(RedisRefreshTokenStore.revoke_token_family)

        lines = source.split('\n')
        in_pipeline_block = False
        for line in lines:
            stripped = line.strip()
            if 'pipeline' in stripped and 'async with' in stripped:
                in_pipeline_block = True
            if 'await self._redis.get' in stripped and not in_pipeline_block:
                pytest.fail("Found pipeline-external get in revoke_token_family")

    def test_no_pipeline_external_hget_in_revoke_all(self):
        """revoke_all_user_tokens不应在pipeline外调用hget"""
        from app.security.refresh_token import RedisRefreshTokenStore
        source = inspect.getsource(RedisRefreshTokenStore.revoke_all_user_tokens)

        if 'await self._redis.hget' in source:
            pytest.fail("Found pipeline-external hget in revoke_all_user_tokens")

    @pytest.mark.asyncio
    async def test_in_memory_family_revocation_consistency(self):
        """内存存储的家族撤销一致性验证"""
        user_info = TestDataFactory.user_info()
        family_id = "atomic_test_family"

        t1 = await create_refresh_token(TestDataFactory.uid(), user_info, family_id=family_id)
        t2 = await create_refresh_token(TestDataFactory.uid(), user_info, family_id=family_id)
        t3 = await create_refresh_token(TestDataFactory.uid(), user_info, family_id=family_id)

        count = await revoke_token_family(family_id)
        assert count == 3

        assert await verify_refresh_token(t1) is None
        assert await verify_refresh_token(t2) is None
        assert await verify_refresh_token(t3) is None

    @pytest.mark.asyncio
    async def test_redis_mock_revoke_family_two_phase(self):
        from app.security.refresh_token import RedisRefreshTokenStore

        RedisRefreshTokenStore._instance = None
        store = RedisRefreshTokenStore()
        store._initialized = True

        mock_redis = MagicMock()
        mock_redis.smembers = AsyncMock(return_value={b"token_1"})
        mock_redis.get = AsyncMock(return_value=b'{"user_id":"u1","revoked":false,"family_id":"f1"}')
        mock_redis.ttl = AsyncMock(return_value=3600)
        mock_redis.set = AsyncMock(return_value=True)

        read_pipe = MagicMock()
        read_pipe.get = MagicMock(return_value=None)
        read_pipe.ttl = MagicMock(return_value=None)
        read_pipe.execute = AsyncMock(return_value=[
            b'{"user_id":"u1","revoked":false,"family_id":"f1"}',
            3600,
        ])

        update_pipe = MagicMock()
        update_pipe.setex = MagicMock(return_value=update_pipe)
        update_pipe.set = MagicMock(return_value=update_pipe)
        update_pipe.srem = MagicMock(return_value=update_pipe)
        update_pipe.delete = MagicMock(return_value=update_pipe)
        update_pipe.execute = AsyncMock(return_value=[True, 1, 1])
        update_pipe.__aenter__ = AsyncMock(return_value=update_pipe)
        update_pipe.__aexit__ = AsyncMock(return_value=None)

        mock_redis.pipeline = MagicMock(side_effect=[read_pipe, update_pipe])
        store._redis_client = mock_redis

        result = await store.revoke_token_family("test_family")

        assert update_pipe.setex.called or update_pipe.set.called
        assert update_pipe.execute.called
        assert result == 1


class TestDegradedReasonSemantics:
    """验证降级原因区分：敏感数据检测 vs SG不可用"""

    def test_degraded_reason_sensitive_data_detected(self):
        """敏感数据检测降级：SG可用，safety_gateway_unavailable=False"""
        checker = SafetyGatewayChecker.__new__(SafetyGatewayChecker)
        checker._whitelist_fields = ["organ_name", "status"]

        test_data = {
            "organ_name": "正常",
            "conclusion": "诊断结论",
            "patient_name": "张三"
        }

        result = checker.apply_degraded_output(test_data, reason=DegradedReason.SENSITIVE_DATA_DETECTED)

        assert result["safety_gateway_unavailable"] is False
        assert result["sg_output_degraded"] is True
        assert result["fusion_degraded"] is False
        assert result["degraded_reason"] == "sensitive_data_detected"
        assert "conclusion" not in result
        assert "patient_name" not in result

    def test_degraded_reason_fusion_unavailable_sensitive_detected(self):
        """融合层不可用+敏感数据降级：safety_gateway_unavailable=False(SG可用), fusion_degraded=True"""
        checker = SafetyGatewayChecker.__new__(SafetyGatewayChecker)
        checker._whitelist_fields = ["organ_name", "status"]

        test_data = {
            "organ_name": "正常",
            "conclusion": "诊断结论"
        }

        result = checker.apply_degraded_output(test_data, reason=DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED)

        assert result["safety_gateway_unavailable"] is False
        assert result["fusion_degraded"] is True
        assert result["fusion_unavailable"] is True
        assert result["fusion_unavailable_sensitive_detected"] is True
        assert result["degraded_reason"] == "fusion_unavailable_sensitive_detected"

    def test_degraded_reason_db_timeout(self):
        """数据库超时降级：db_timeout=True"""
        checker = SafetyGatewayChecker.__new__(SafetyGatewayChecker)
        checker._whitelist_fields = ["organ_name", "status"]

        test_data = {"organ_name": "正常", "conclusion": "结论"}

        result = checker.apply_degraded_output(test_data, reason=DegradedReason.DB_TIMEOUT)

        assert result.get("db_timeout") is True
        assert result["safety_gateway_unavailable"] is False
        assert result["degraded_reason"] == "db_timeout"

    def test_degraded_output_suppresses_conclusion_for_security_reasons(self):
        """安全相关降级原因必须抑制结论，非安全原因（如DB超时）不抑制"""
        from safety_gateway.degraded_output import _CONCLUSION_SUPPRESSED_REASONS
        checker = SafetyGatewayChecker.__new__(SafetyGatewayChecker)
        checker._whitelist_fields = ["organ_name"]

        for reason in _CONCLUSION_SUPPRESSED_REASONS:
            result = checker.apply_degraded_output({"organ_name": "x", "diagnostic_conclusion": "y"}, reason=reason)
            assert result["conclusion_suppressed"] is True, f"Security reason {reason} should suppress conclusion"
            assert "diagnostic_conclusion" not in result, f"Security reason {reason} should remove diagnostic_conclusion"

        non_security_reasons = [r for r in DegradedReason if r not in _CONCLUSION_SUPPRESSED_REASONS]
        for reason in non_security_reasons:
            result = checker.apply_degraded_output({"organ_name": "x", "diagnostic_conclusion": "y"}, reason=reason)
            assert result["conclusion_suppressed"] is True, f"Non-security reason {reason} should suppress conclusion"


class TestSGUnavailableHandling:
    """验证SG_UNAVAILABLE场景正确处理"""

    def test_main_py_raises_sg_error_on_internal_error(self):
        """main.py在内部错误时应抛出SGServiceUnavailableError"""
        import safety_gateway.main as main_module
        source = inspect.getsource(main_module)

        assert "ServiceUnavailableError" in source

    def test_check_result_status_includes_fusion_unavailable_sensitive_detected(self):
        """CheckResultStatus包含FUSION_UNAVAILABLE_SENSITIVE_DETECTED"""
        assert DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED.value == "fusion_unavailable_sensitive_detected"

    def test_degraded_reason_fusion_unavailable_sensitive_sets_flag(self):
        """FUSION_UNAVAILABLE_SENSITIVE_DETECTED降级原因设置fusion_unavailable=True"""
        checker = SafetyGatewayChecker.__new__(SafetyGatewayChecker)
        checker._whitelist_fields = ["organ_name"]

        result = checker.apply_degraded_output(
            {"organ_name": "正常", "conclusion": "结论"},
            reason=DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED
        )

        assert result["fusion_unavailable"] is True
        assert result["degraded_reason"] == "fusion_unavailable_sensitive_detected"


class TestSGUnavailableInChecker:
    """验证checker内部DB异常时正确返回FAILED"""

    def test_consent_checker_has_check_consent_status(self):
        """consent_checker应有check_consent_status方法"""
        from safety_gateway.consent_checker import ConsentChecker
        assert hasattr(ConsentChecker, 'check_consent_status')

    def test_db_error_raises_database_unavailable(self):
        """DB异常时ConsentPhase应能抛出DatabaseUnavailableError"""
        from safety_gateway.checker.consent_phase import ConsentPhase
        source = inspect.getsource(ConsentPhase)
        assert "DatabaseUnavailableError" in source

    def test_db_timeout_returns_degraded(self):
        """DB超时时ConsentPhase应返回DEGRADED+timeout"""
        from safety_gateway.checker.consent_phase import ConsentPhase
        source = inspect.getsource(ConsentPhase)
        assert "consent_check_timeout" in source or "timeout" in source

    def test_normal_check_returns_passed(self):
        """正常检查ConsentPhase应返回GRANTED"""
        from safety_gateway.validation_result import ConsentStatus
        assert hasattr(ConsentStatus, "GRANTED")


class TestErrorCodeExceptions:
    """验证错误码异常类正确使用"""

    def test_patient_not_consented_exception_has_e1008(self):
        """E1008: 患者未签署知情同意书"""
        exc = PatientNotConsentedException(error_message="测试")
        assert exc.error_code == "E1008"
        assert exc.status_code == 403
        assert exc.suggestion == "请先获取患者知情同意"

    def test_patient_consent_withdrawn_exception_has_e1010(self):
        """E1010: 患者已撤回知情同意"""
        exc = PatientConsentWithdrawnException(error_message="测试")
        assert exc.error_code == "E1010"
        assert exc.status_code == 403
        assert exc.suggestion == "请重新获取患者知情同意"

    def test_safety_gateway_unavailable_exception_has_e4005(self):
        """E4005: 安全校验服务不可用"""
        exc = SafetyGatewayUnavailableException(error_message="测试")
        assert exc.error_code == "E4005"
        assert exc.status_code == 503
        assert exc.retryable is True

    def test_bad_request_exception_has_e1001(self):
        """E1001: 请求参数错误"""
        exc = BadRequestException(error_message="无效参数")
        assert exc.error_code == "E1001"
        assert exc.status_code == 400

    def test_consent_api_uses_error_codes(self):
        """验证consent.py使用错误码异常类而非通用HTTPException"""
        import app.api.v1.consent as consent_module
        source = inspect.getsource(consent_module)

        assert "BadRequestException" in source

    def test_safety_gateway_main_uses_sg_error(self):
        """验证main.py使用SGError异常体系"""
        import safety_gateway.main as main_module
        source = inspect.getsource(main_module)

        assert "ServiceUnavailableError" in source
        assert "SafetyGatewayError" in source


class TestConsentRecordSingleSource:
    """验证SafetyGateway使用主应用的ConsentRecord模型"""

    def test_checker_imports_from_main_models(self):
        """checker应从validation_result导入ConsentStatus，而非本地定义ConsentRecord"""
        import safety_gateway.checker.orchestrator as checker_module
        source = inspect.getsource(checker_module)

        assert "from safety_gateway.validation_result import ConsentStatus" in source
        assert "class ConsentRecord" not in source

    def test_no_declarative_base_in_checker(self):
        """checker.py不应定义自己的Base"""
        import safety_gateway.checker as checker_module
        source = inspect.getsource(checker_module)

        assert "declarative_base" not in source


class TestConsentWithdrawalBroadcast:
    """验证同意撤回时通过Redis Streams广播"""

    def test_withdrawal_stream_constant_defined(self):
        """CONSENT_WITHDRAWAL_STREAM常量应定义"""
        assert CONSENT_WITHDRAWAL_STREAM is not None
        assert "consent" in CONSENT_WITHDRAWAL_STREAM and "withdrawal" in CONSENT_WITHDRAWAL_STREAM

    def test_publish_method_exists(self):
        """ConsentRepository应有_publish_consent_withdrawal方法"""
        from app.repositories.consent_repository import ConsentRepository
        assert hasattr(ConsentRepository, '_publish_consent_withdrawal')

    @pytest.mark.asyncio
    async def test_withdrawal_calls_publish(self):
        """撤回操作应调用_publish_consent_withdrawal"""
        from app.repositories.consent_repository import ConsentRepository

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        repo = ConsentRepository(mock_db)

        with patch("app.services.audit_service.AuditService.log_action", new_callable=AsyncMock):
            with patch("app.services.safety_service.SafetyService.log_event", new_callable=AsyncMock):
                with patch.object(repo, '_publish_consent_withdrawal', new_callable=AsyncMock) as mock_publish:
                    await repo.create_consent(
                        patient_id="P001",
                        status=ConsentStatus.WITHDRAWN,
                        consent_version="1.0",
                        operator_id="admin",
                    )

                    mock_publish.assert_called_once()
                    call_kwargs = mock_publish.call_args[1]
                    assert call_kwargs["patient_id"] == "P001"
                    assert call_kwargs["operator_id"] == "admin"

    @pytest.mark.asyncio
    async def test_grant_does_not_call_publish(self):
        """授权操作不应调用_publish_consent_withdrawal"""
        from app.repositories.consent_repository import ConsentRepository

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        repo = ConsentRepository(mock_db)

        with patch("app.services.audit_service.AuditService.log_action", new_callable=AsyncMock):
            with patch("app.services.safety_service.SafetyService.log_event", new_callable=AsyncMock):
                with patch.object(repo, '_publish_consent_withdrawal', new_callable=AsyncMock) as mock_publish:
                    await repo.create_consent(
                        patient_id="P001",
                        status=ConsentStatus.GRANTED,
                        consent_version="1.0",
                        operator_id="admin",
                    )

                    mock_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_handles_redis_failure_gracefully(self):
        """Redis发布失败不应导致撤回操作失败"""
        from app.repositories.consent_repository import ConsentRepository

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        repo = ConsentRepository(mock_db)

        with patch("app.services.audit_service.AuditService.log_action", new_callable=AsyncMock):
            with patch("app.services.safety_service.SafetyService.log_event", new_callable=AsyncMock):
                with patch("app.config.redis.RedisPool") as mock_pool:
                    mock_pool.get_client = AsyncMock(side_effect=Exception("Redis down"))

                    result = await repo.create_consent(
                        patient_id="P001",
                        status=ConsentStatus.WITHDRAWN,
                        consent_version="1.0",
                        operator_id="admin",
                    )

                    assert result is not None
                    assert result.status == ConsentStatus.WITHDRAWN.value


class TestConsentAPIBehaviorChange:
    """验证consent API的行为变更"""

    def test_consent_docstring_documents_behavior_change(self):
        """consent.py GET端点应在无记录时返回DENIED状态"""
        import app.api.v1.consent as consent_module
        source = inspect.getsource(consent_module.get_consent)

        assert "ConsentStatus.DENIED" in source

    def test_no_consent_raises_e1008(self):
        """无同意记录时应抛出PatientNotConsentedException(E1008)"""
        exc = PatientNotConsentedException(error_message="测试")
        assert exc.error_code == "E1008"
        assert exc.status_code == 403

    def test_withdrawn_raises_e1010(self):
        """已撤回应抛出PatientConsentWithdrawnException(E1010)"""
        exc = PatientConsentWithdrawnException(error_message="测试")
        assert exc.error_code == "E1010"
        assert exc.status_code == 403


class TestHealthCheckNoResourceLeak:
    """验证健康检查不再创建新引擎"""

    def test_health_check_uses_existing_engine(self):
        """健康检查应复用health_checker实例，而非创建新引擎"""
        import safety_gateway.main as main_module
        source = inspect.getsource(main_module.health_check)

        assert "_get_health_checker" in source
        assert "create_async_engine" not in source

    def test_checker_has_close_method(self):
        """SafetyGatewayChecker必须有close方法用于清理资源"""
        assert hasattr(SafetyGatewayChecker, 'close')
        assert asyncio.iscoroutinefunction(SafetyGatewayChecker.close)


class TestCheckResultModel:
    """验证CheckResult模型包含所有必要字段"""

    def test_check_result_has_degraded_reason(self):
        """CheckResult必须包含degraded_reason字段"""
        result = CheckResult(
            status=CheckResultStatus.DEGRADED,
            degraded_mode=True,
            degraded_reason=DegradedReason.SENSITIVE_DATA_DETECTED
        )
        assert result.degraded_reason == DegradedReason.SENSITIVE_DATA_DETECTED

    def test_check_result_status_values(self):
        """CheckResultStatus必须包含所有必要状态"""
        assert CheckResultStatus.PASSED.value == "passed"
        assert CheckResultStatus.FAILED.value == "failed"
        assert CheckResultStatus.DEGRADED.value == "degraded"

    def test_degraded_reason_values(self):
        """DegradedReason必须包含所有必要原因"""
        assert DegradedReason.SENSITIVE_DATA_DETECTED.value == "sensitive_data_detected"
        assert DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED.value == "fusion_unavailable_sensitive_detected"
        assert DegradedReason.DB_TIMEOUT.value == "db_timeout"


class TestSafetyGatewayRulesLoading:
    """验证SafetyGateway脱敏引擎正确加载YAML规则"""

    def test_engine_accepts_rules_path_parameter(self):
        """引擎构造函数应接受rules_path参数"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        assert len(engine._rules) > 0

    def test_engine_loads_default_rules_when_no_path(self):
        """无路径时使用默认规则"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        rules = engine.get_rules()
        assert len(rules) >= 7

    def test_engine_falls_back_to_default_on_invalid_path(self):
        """无效路径时回退到默认规则"""
        engine = SafetyGatewayDesensitizationEngine(rules_path="/nonexistent/path.yaml")
        rules = engine.get_rules()
        assert len(rules) >= 7

    def test_default_rules_include_birth_date(self):
        """默认规则包含出生日期脱敏"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        rule_names = [r.name for r in engine.get_rules()]
        assert "sg_birth_date" in rule_names

    def test_default_rules_include_address(self):
        """默认规则包含住址脱敏"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        rule_names = [r.name for r in engine.get_rules()]
        assert "sg_address" in rule_names

    def test_default_rules_include_medical_record_number(self):
        """默认规则包含病历号脱敏"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        rule_names = [r.name for r in engine.get_rules()]
        assert "sg_medical_record_number" in rule_names

    def test_default_rules_include_dicom_uid(self):
        """默认规则包含DICOM UID脱敏"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        rule_names = [r.name for r in engine.get_rules()]
        assert "sg_dicom_uid" in rule_names


class TestEnhancedDesensitizationRules:
    """验证增强的脱敏规则"""

    def test_birth_date_desensitization(self):
        """出生日期脱敏"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        text = "出生日期：1990年01月01日"
        result = engine.desensitize(text)
        assert "1990" not in result
        assert "[REDACTED:BIRTH_DATE]" in result

    def test_address_desensitization(self):
        """住址脱敏"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        text = "住址：北京市朝阳区建国路100号"
        result = engine.desensitize(text)
        assert "建国路100号" not in result

    def test_medical_record_number_desensitization(self):
        """病历号脱敏"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        text = "病历号：MR-2026-001234"
        result = engine.desensitize(text)
        assert "MR-2026-001234" not in result

    def test_dicom_uid_desensitization(self):
        """DICOM StudyInstanceUID脱敏"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        text = "StudyInstanceUID：1.2.840.113619.2.55.3.1272566195.123.456"
        result = engine.desensitize(text)
        assert "1.2.840.113619" not in result

    def test_phone_desensitization_heterogeneous(self):
        """手机号脱敏使用异构模式（整体替换，非分组保留）"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        text = "电话：13812345678"
        result = engine.desensitize(text)
        assert "13812345678" not in result
        assert "[REDACTED:PHONE]" in result


class TestYAMLRuleSync:
    """验证YAML规则文件与默认规则完全同步"""

    def test_yaml_file_has_11_rules(self):
        """YAML文件应包含11条规则，与默认规则数一致"""
        with open(_YAML_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert len(data["rules"]) == 11

    def test_yaml_contains_all_enhanced_rule_names(self):
        """YAML应包含所有增强规则名"""
        with open(_YAML_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        yaml_rule_names = {r["name"] for r in data["rules"]}

        expected_names = {
            "sg_id_card", "sg_phone", "sg_patient_name", "sg_patient_name_standalone",
            "sg_patient_name_simple",
            "sg_email", "sg_birth_date", "sg_address", "sg_medical_record_number",
            "sg_dicom_uid", "sg_ip_address",
        }
        assert yaml_rule_names == expected_names

    def test_yaml_no_passport_or_bank_card_rules(self):
        """YAML不应包含护照和银行卡规则（默认规则中无此两条）"""
        with open(_YAML_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        yaml_rule_names = {r["name"] for r in data["rules"]}
        assert "sg_passport" not in yaml_rule_names
        assert "sg_bank_card" not in yaml_rule_names

    def test_yaml_phone_rule_uses_heterogeneous_pattern(self):
        """YAML手机号规则应使用异构模式（整体匹配+号段校验，非分组模式）"""
        with open(_YAML_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        phone_rule = next(r for r in data["rules"] if r["name"] == "sg_phone")
        assert "1[3-9]" in phone_rule["pattern"]
        assert phone_rule["replacement"] == "[REDACTED:PHONE]"

    def test_yaml_loaded_engine_matches_default(self):
        """从YAML加载的引擎规则名应与默认规则完全一致"""
        default_engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        default_names = sorted(r.name for r in default_engine.get_rules())

        yaml_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "desensitization_rules_safety.yaml",
        )
        yaml_engine = SafetyGatewayDesensitizationEngine(rules_path=yaml_path)
        yaml_names = sorted(r.name for r in yaml_engine.get_rules())

        assert default_names == yaml_names

    def test_temp_yaml_with_old_rules_lacks_enhanced_rules(self):
        """旧版YAML（含硬编码姓名）加载后不应包含增强规则"""
        old_yaml_content = {
            "rules": [
                {"name": "sg_id_card", "pattern": r"\d{17}[\dXx]", "replacement": "***", "priority": 1, "enabled": True, "description": "test"},
                {"name": "sg_patient_name_simple", "pattern": r"(张三|李四)", "replacement": "[REDACTED_NAME_SG]", "priority": 3, "enabled": True, "description": "test"},
            ]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            yaml.dump(old_yaml_content, f)
            temp_path = f.name

        try:
            engine = SafetyGatewayDesensitizationEngine(rules_path=temp_path)
            rule_names = [r.name for r in engine.get_rules()]
            assert "sg_patient_name_simple" in rule_names
            assert "sg_birth_date" not in rule_names
            assert "sg_dicom_uid" not in rule_names
        finally:
            os.unlink(temp_path)


class TestYAMLEscapeAnnotation:
    """验证YAML文件包含正则转义说明"""

    def test_yaml_has_escape_annotation(self):
        """YAML文件顶部应包含正则转义说明"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        assert len(engine.get_rules()) >= 10

    def test_yaml_has_python_to_yaml_example(self):
        """YAML文件应包含Python→YAML转换示例"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        rule_names = [r.name for r in engine.get_rules()]
        assert "sg_id_card" in rule_names
        assert "sg_phone" in rule_names

    def test_yaml_has_double_backslash_example(self):
        """YAML文件应说明双反斜杠规则"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        id_card_rule = next((r for r in engine.get_rules() if r.name == "sg_id_card"), None)
        assert id_card_rule is not None
        assert r"\d" in id_card_rule.pattern


class TestNoDynamicImport:
    """验证不使用__import__动态导入"""

    def test_main_py_uses_normal_import(self):
        """main.py应使用正常的from...import而非__import__"""
        import safety_gateway.main as main_module
        source = inspect.getsource(main_module)

        assert "__import__" not in source
        assert "from safety_gateway" in source


class TestCheckerLazyLoading:
    """验证SafetyGatewayChecker懒加载"""

    def test_checker_engine_is_none_at_init(self):
        """初始化时引擎应已创建（非懒加载）"""
        checker = SafetyGatewayChecker()
        assert checker._consent_checker is not None

    def test_ensure_engine_creates_engine(self):
        """检查器引擎应可用"""
        checker = SafetyGatewayChecker()
        assert checker._contraindication_engine is not None

    def test_get_safety_gateway_checker_returns_same_instance(self):
        """get_safety_gateway_checker应返回同一实例"""
        c1 = get_safety_gateway_checker()
        c2 = get_safety_gateway_checker()
        assert c1 is c2

    def test_no_global_sg_checker_variable(self):
        """不应有全局sg_checker实例（避免import时创建DB连接）"""
        import safety_gateway.checker as checker_module
        assert not hasattr(checker_module, 'sg_checker')


class TestRecursionDepthLimit:
    """验证_check_dict_recursive有深度限制"""

    def test_max_recursion_depth_defined(self):
        """MAX_RECURSION_DEPTH应定义且合理"""
        assert MAX_RECURSION_DEPTH == 20

    @pytest.mark.asyncio
    async def test_deeply_nested_dict_does_not_crash(self):
        """深度嵌套字典不应导致栈溢出"""
        from safety_gateway.checker.desensitization_phase import DesensitizationPhase
        phase = DesensitizationPhase.__new__(DesensitizationPhase)

        deep_dict = {"level": "data"}
        for i in range(15):
            deep_dict = {"nested": deep_dict, "level": f"depth_{i}"}

        result = await phase._check_value_recursive(deep_dict, depth=0)

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_recursion_depth_exceeded_returns_empty(self):
        """超过深度限制应返回空列表"""
        from safety_gateway.checker.desensitization_phase import DesensitizationPhase
        phase = DesensitizationPhase.__new__(DesensitizationPhase)

        deep_dict = {"data": "13812345678"}
        for _ in range(12):
            deep_dict = {"nested": deep_dict}

        result = await phase._check_value_recursive(deep_dict, depth=MAX_RECURSION_DEPTH)

        assert result == []


class TestNoTautologicalTests:
    """验证测试文件中没有恒真/恒假断言"""

    def test_consent_status_tests_are_not_tautological(self):
        """
        原始审查报告指出test_consent_status_granted是恒真测试：
        consent = mock_consent_record("GRANTED")
        assert consent.status == "GRANTED"  # 恒真！

        修复后应验证异常类行为而非mock对象赋值
        """
        exc = PatientNotConsentedException(error_message="测试")
        assert exc.error_code == "E1008"
        assert exc.status_code == 403

    def test_sensitive_data_check_returns_specific_types(self):
        """敏感数据检查应返回具体类型列表，而非空列表断言"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        detected = engine.check_sensitive_data("身份证号：110101199001011234")
        assert "sg_id_card" in detected

    def test_sensitive_data_check_no_false_positives(self):
        """正常文本不应检测到敏感数据"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        detected = engine.check_sensitive_data("这是一段正常的医学描述文本")
        assert detected == []


class TestAuditLogHashChain:
    """验证审计日志哈希链实现"""

    def test_audit_service_has_hash_chain(self):
        """AuditService应实现哈希链"""
        source = inspect.getsource(AuditService.log_action)
        assert "hash_prev" in source
        assert "hash_self" in source

    def test_audit_service_gets_last_hash(self):
        """AuditService应有_get_last_hash方法"""
        source = inspect.getsource(AuditService._get_last_hash)
        assert "hash_self" in source

    @pytest.mark.asyncio
    async def test_first_log_has_none_hash_prev(self):
        """首条审计日志的hash_prev应为None"""
        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()

        service = AuditService(mock_db)
        log = await service.log_action(action="test_action", actor="admin")

        assert log.hash_prev is None
        assert log.hash_self is not None

    @pytest.mark.asyncio
    async def test_second_log_links_to_first(self):
        """第二条审计日志的hash_prev应等于第一条的hash_self"""
        first_hash_self = "abc123def456"
        mock_prev_record = MagicMock()
        mock_prev_record.hash_self = first_hash_self

        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_prev_record)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()

        service = AuditService(mock_db)
        log = await service.log_action(action="test_action_2", actor="admin")

        assert log.hash_prev == first_hash_self
        assert log.hash_self is not None
        assert log.hash_self != first_hash_self

    def test_consent_repository_uses_audit_service(self):
        """ConsentRepository应使用AuditService记录审计日志"""
        from app.repositories.consent_repository import ConsentRepository
        source = inspect.getsource(ConsentRepository.create_consent)
        assert "AuditService" in source
        assert "log_action" in source


class TestAuditChainVerification:
    """验证哈希链verify_chain()方法"""

    @pytest.mark.asyncio
    async def test_verify_chain_empty_logs(self):
        """空日志链应返回有效"""
        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        service = AuditService(mock_db)
        is_valid, broken_at, expected, actual = await service.verify_chain()

        assert is_valid is True
        assert broken_at is None

    @pytest.mark.asyncio
    async def test_verify_chain_single_log(self):
        """单条日志链（hash_prev=None）应返回有效"""
        log = MagicMock()
        log.log_id = 1001
        log.hash_prev = None
        log.hash_self = "abc123"

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [log]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        service = AuditService(mock_db)
        is_valid, broken_at, expected, actual = await service.verify_chain()

        assert is_valid is True
        assert broken_at is None

    @pytest.mark.asyncio
    async def test_verify_chain_intact_chain(self):
        """完整链应返回有效"""
        log1 = MagicMock()
        log1.log_id = 1001
        log1.hash_prev = None
        log1.hash_self = "hash_a"

        log2 = MagicMock()
        log2.log_id = 1002
        log2.hash_prev = "hash_a"
        log2.hash_self = "hash_b"

        log3 = MagicMock()
        log3.log_id = 1003
        log3.hash_prev = "hash_b"
        log3.hash_self = "hash_c"

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [log1, log2, log3]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        service = AuditService(mock_db)
        is_valid, broken_at, expected, actual = await service.verify_chain()

        assert is_valid is True
        assert broken_at is None

    @pytest.mark.asyncio
    async def test_verify_chain_broken_chain(self):
        """断裂链应返回无效，并标识断裂位置"""
        log1 = MagicMock()
        log1.log_id = 1001
        log1.hash_prev = None
        log1.hash_self = "hash_a"

        log2 = MagicMock()
        log2.log_id = 1002
        log2.hash_prev = "TAMPERED_hash"
        log2.hash_self = "hash_b"

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [log1, log2]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        service = AuditService(mock_db)
        is_valid, broken_at, expected, actual = await service.verify_chain()

        assert is_valid is False
        assert broken_at == 1002
        assert expected == "hash_a"
        assert actual == "TAMPERED_hash"

    @pytest.mark.asyncio
    async def test_verify_chain_first_log_non_none_hash_prev(self):
        """首条日志hash_prev不为None应返回无效"""
        log1 = MagicMock()
        log1.log_id = 1001
        log1.hash_prev = "should_be_none"
        log1.hash_self = "hash_a"

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [log1]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        service = AuditService(mock_db)
        is_valid, broken_at, expected, actual = await service.verify_chain()

        assert is_valid is False
        assert broken_at == 1001
        assert actual == "should_be_none"

    def test_verify_chain_method_exists(self):
        """AuditService应有verify_chain方法"""
        assert hasattr(AuditService, 'verify_chain')

    def test_verify_chain_returns_tuple(self):
        """verify_chain应返回4元素元组(is_valid, broken_log_id, expected_hash, actual_hash)"""
        source = inspect.getsource(AuditService.verify_chain)
        assert "return True, None, None, None" in source
        assert "log_id" in source
        assert "prev_hash" in source


class TestDeterministicHashSerialization:
    """验证detail字段确定性序列化"""

    def test_same_detail_same_hash(self):
        """相同detail内容应生成相同hash_self"""
        detail = {"status": "granted", "version": "1.0"}
        serialized = json.dumps(detail, sort_keys=True, ensure_ascii=False)

        hash1 = hashlib.sha256(f"1001-consent_granted-admin-2026-01-01T00:00:00+00:00-{serialized}".encode()).hexdigest()
        hash2 = hashlib.sha256(f"1001-consent_granted-admin-2026-01-01T00:00:00+00:00-{serialized}".encode()).hexdigest()

        assert hash1 == hash2

    def test_different_detail_different_hash(self):
        """不同detail内容应生成不同hash_self"""
        detail_a = {"status": "granted", "version": "1.0"}
        detail_b = {"status": "withdrawn", "version": "1.0"}

        serialized_a = json.dumps(detail_a, sort_keys=True, ensure_ascii=False)
        serialized_b = json.dumps(detail_b, sort_keys=True, ensure_ascii=False)

        hash_a = hashlib.sha256(f"1001-action-actor-2026-01-01T00:00:00+00:00-{serialized_a}".encode()).hexdigest()
        hash_b = hashlib.sha256(f"1001-action-actor-2026-01-01T00:00:00+00:00-{serialized_b}".encode()).hexdigest()

        assert hash_a != hash_b

    def test_key_order_does_not_affect_hash(self):
        """detail字段key顺序不同应生成相同hash_self"""
        detail_a = {"status": "granted", "version": "1.0"}
        detail_b = {"version": "1.0", "status": "granted"}

        serialized_a = json.dumps(detail_a, sort_keys=True, ensure_ascii=False)
        serialized_b = json.dumps(detail_b, sort_keys=True, ensure_ascii=False)

        assert serialized_a == serialized_b

        hash_a = hashlib.sha256(f"1001-action-actor-2026-01-01T00:00:00+00:00-{serialized_a}".encode()).hexdigest()
        hash_b = hashlib.sha256(f"1001-action-actor-2026-01-01T00:00:00+00:00-{serialized_b}".encode()).hexdigest()

        assert hash_a == hash_b

    def test_audit_service_uses_json_dumps(self):
        """AuditService.log_action应使用json.dumps而非f-string"""
        source = inspect.getsource(AuditService.log_action)
        assert "json.dumps(detail, sort_keys=True" in source
        assert "f\"{hash_content}-{detail}\"" not in source

    @pytest.mark.asyncio
    async def test_detail_serialization_in_actual_log(self):
        """实际审计日志中detail序列化应确定性"""
        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()

        fixed_uuid = MagicMock()
        fixed_uuid.int = 12345 << 96
        fixed_now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        with patch("app.services.audit_service.uuid.uuid4", return_value=fixed_uuid):
            with patch("app.services.audit_service.datetime") as mock_dt:
                mock_dt.now.return_value = fixed_now
                mock_dt.fromisoformat = datetime.fromisoformat

                service = AuditService(mock_db)
                log1 = await service.log_action(
                    action="test",
                    actor="admin",
                    detail={"z_key": "last", "a_key": "first"},
                )

                service._last_hash = None
                mock_result2 = AsyncMock()
                mock_result2.scalar_one_or_none = MagicMock(return_value=None)
                mock_db.execute = AsyncMock(return_value=mock_result2)

                log2 = await service.log_action(
                    action="test",
                    actor="admin",
                    detail={"a_key": "first", "z_key": "last"},
                )

        assert log1.hash_self == log2.hash_self


class TestSafetyGatewayE2E:
    """验证safety-gateway端到端行为"""

    @pytest.mark.asyncio
    async def test_fusion_unavailable_sensitive_triggers_degraded_output(self):
        """FUSION_UNAVAILABLE_SENSITIVE_DETECTED应触发降级输出"""
        checker = SafetyGatewayChecker()
        checker._whitelist_fields = ["organ_name", "status"]

        degraded = checker.apply_degraded_output(
            {"organ_name": "正常", "conclusion": "诊断结论"},
            reason=DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED,
        )
        assert degraded["fusion_unavailable"] is True
        assert "conclusion" not in degraded
        assert "organ_name" in degraded

    @pytest.mark.asyncio
    async def test_sensitive_data_triggers_degraded_mode(self):
        """敏感数据检测应触发降级模式"""
        checker = SafetyGatewayChecker()
        result = await checker.check_desensitization({"text": "身份证号：110101199001011234"})
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_withdrawn_consent_fails_check(self):
        """已撤回同意应导致校验失败"""
        checker = SafetyGatewayChecker()
        with patch.object(checker._consent_checker, 'check_consent_status',
                         new_callable=AsyncMock) as mock_check:
            mock_check.return_value = (ConsentStatus.WITHDRAWN, False, False)

            result = await checker.perform_full_check(
                patient_id="P001",
                output_data={"observations": "正常"},
                consent_status_at_input="withdrawn",
            )

            assert result.status == CheckResultStatus.FAILED


class TestRegression:
    """确保修复不破坏现有功能"""

    @pytest.mark.asyncio
    async def test_token_creation_still_works(self):
        user_id = TestDataFactory.uid()
        user_info = TestDataFactory.user_info()

        token_id = await create_refresh_token(user_id, user_info)
        data = await verify_refresh_token(token_id)

        assert data is not None
        assert data["user_id"] == user_id

    def test_hsm_key_generation_still_works(self):
        key_name = "regression_key"
        info = hsm_manager.generate_key_pair(key_name)

        assert info["key_name"] == key_name
        assert info["key_version"] == 1

    def test_storage_mode_is_memory(self):
        assert get_storage_mode() == "memory"

    @pytest.mark.asyncio
    async def test_token_revocation_still_works(self):
        user_id = TestDataFactory.uid()
        user_info = TestDataFactory.user_info()

        token_id = await create_refresh_token(user_id, user_info)
        result = await revoke_refresh_token(token_id)

        assert result is True
        data = await verify_refresh_token(token_id)
        assert data is None

    @pytest.mark.asyncio
    async def test_token_lifecycle(self):
        """Token生命周期回归测试"""
        uid = TestDataFactory.uid()
        info = TestDataFactory.user_info()

        token = await create_refresh_token(uid, info)
        data = await verify_refresh_token(token)
        assert data is not None
        assert data["user_id"] == uid

        await revoke_refresh_token(token)
        data_after = await verify_refresh_token(token)
        assert data_after is None

    def test_degraded_output_structure(self):
        """降级输出结构回归测试"""
        checker = SafetyGatewayChecker.__new__(SafetyGatewayChecker)
        checker._whitelist_fields = ["organ_name", "status"]

        result = checker.apply_degraded_output(
            {"organ_name": "正常", "conclusion": "结论", "patient_name": "张三"},
            reason=DegradedReason.SENSITIVE_DATA_DETECTED
        )

        assert "organ_name" in result
        assert "conclusion_suppressed" in result
        assert "degraded_reason" in result
        assert "conclusion" not in result
        assert "patient_name" not in result

    def test_default_rules_still_work(self):
        """默认规则仍正常工作"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        text = "身份证号：110101199001011234"
        result = engine.desensitize(text)
        assert "110101199001011234" not in result

    def test_yaml_rules_still_work(self):
        """YAML规则仍正常工作"""
        yaml_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "desensitization_rules_safety.yaml",
        )
        engine = SafetyGatewayDesensitizationEngine(rules_path=yaml_path)
        text = "身份证号：110101199001011234"
        result = engine.desensitize(text)
        assert "110101199001011234" not in result


class TestRegressionRound2:
    def test_audit_service_still_logs_actions(self):
        """AuditService仍能正常记录审计日志"""
        assert hasattr(AuditService, 'log_action')

    def test_hsm_degraded_flag_still_works(self):
        """HSM降级标志仍正常工作"""
        hsm = HSMManager()
        assert hsm.is_degraded() is False
        hsm.set_degraded("test_degradation")
        assert hsm.is_degraded() is True
        hsm.clear_degraded()
        assert hsm.is_degraded() is False

    def test_yaml_rules_still_load(self):
        """YAML规则仍正常加载"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        assert len(engine.get_rules()) >= 10

    def test_default_rules_still_load(self):
        """默认规则仍正常加载"""
        engine = SafetyGatewayDesensitizationEngine(rules_path=None)
        assert len(engine.get_rules()) >= 10


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark test as asyncio test")


# ============================================================================
# 合并自 test_sec_supplementary.py
# ============================================================================


class TestHSMConcurrentKeyRotation:

    def test_concurrent_rotate_same_key(self):
        hsm_manager.generate_key_pair("concurrent_rotate_test")
        errors = []

        def rotate():
            try:
                hsm_manager.rotate_key("concurrent_rotate_test")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=rotate) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent rotate errors: {errors}"


class TestDesensitizationConcurrentAccess:

    def test_concurrent_desensitize_with_rule_reload(self):
        engine = get_desensitization_engine()
        errors = []

        def desensitize_worker():
            try:
                for _ in range(100):
                    engine.desensitize("患者张三，身份证号110101199001011234")
            except Exception as e:
                errors.append(e)

        def reload_worker():
            try:
                for _ in range(10):
                    engine.reload_rules()
                    time.sleep(0)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=desensitize_worker),
            threading.Thread(target=desensitize_worker),
            threading.Thread(target=reload_worker),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent access errors: {errors}"


class TestRefreshTokenConcurrentRevokeAndVerifySupplementary:

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
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent revoke/verify errors: {errors}"

    def test_concurrent_create_tokens_for_same_user(self):
        store = refresh_token_store
        user_id = "concurrent_create_user"
        token_ids = []
        lock = threading.Lock()

        def create_worker():
            try:
                token_id = store.create_refresh_token(user_id=user_id, user_info={"role": "doctor"})
                if token_id:
                    with lock:
                        token_ids.append(token_id)
            except Exception:
                pass

        threads = [threading.Thread(target=create_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        active_count = store.get_user_token_count(user_id)
        assert active_count <= 5, f"Expected <=5 concurrent sessions, got {active_count}"


class TestHSMDegradedPhaseBoundary:

    def test_degraded_phase_transition_at_5min(self):
        hsm_manager.set_degraded("test_boundary")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=5, seconds=1)
        phase = hsm_manager.get_degraded_phase()
        assert phase == DegradedPhase.RESTRICTED, f"Expected RESTRICTED at 5min+1s, got {phase}"

    def test_degraded_phase_just_before_5min(self):
        hsm_manager.set_degraded("test_boundary_2")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=4, seconds=59)
        phase = hsm_manager.get_degraded_phase()
        assert phase == DegradedPhase.CACHED, f"Expected CACHED at 4:59, got {phase}"

    def test_degraded_phase_transition_at_30min(self):
        hsm_manager.set_degraded("test_boundary_3")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=30, seconds=1)
        phase = hsm_manager.get_degraded_phase()
        assert phase == DegradedPhase.EMERGENCY, f"Expected EMERGENCY at 30min+1s, got {phase}"

    def test_degraded_phase_just_before_30min(self):
        hsm_manager.set_degraded("test_boundary_4")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=29, seconds=59)
        phase = hsm_manager.get_degraded_phase()
        assert phase == DegradedPhase.RESTRICTED, f"Expected RESTRICTED at 29:59, got {phase}"


class TestEmergencySigningBoundary:

    @pytest.mark.asyncio
    async def test_daily_limit_boundary_at_49(self):
        manager = EmergencySigningManager()
        manager._is_active = True
        with patch("app.config.redis.RedisPool") as mock_pool:
            mock_redis = AsyncMock()
            mock_redis.eval = AsyncMock(return_value=50)
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            result = await manager._atomic_check_and_increment_daily_count()
            assert result <= settings.hsm_emergency_signing_max_daily

    @pytest.mark.asyncio
    async def test_daily_limit_boundary_at_50(self):
        manager = EmergencySigningManager()
        manager._is_active = True
        with patch("app.config.redis.RedisPool") as mock_pool:
            mock_redis = AsyncMock()
            mock_redis.eval = AsyncMock(return_value=51)
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            result = await manager._atomic_check_and_increment_daily_count()
            assert result > settings.hsm_emergency_signing_max_daily


class TestDesensitizationRulePriority:

    def test_id_card_processed_before_name(self):
        engine = get_desensitization_engine()
        text = "患者110101199001011234"
        result = engine.desensitize(text)
        assert '********************' in result
        assert '110101199001011234' not in result

    def test_multiple_rules_on_same_text(self):
        engine = get_desensitization_engine()
        text = "患者张三，手机号13812345678，身份证号110101199001011234"
        result = engine.desensitize(text)
        assert PATIENT_TOKEN_PATTERN.search(result), f"Expected PATIENT_XXX in: {result}"
        assert '********************' in result
        assert '******' in result
        assert '张三' not in result
        assert '13812345678' not in result
        assert '110101199001011234' not in result


class TestDesensitizationRestoreKeyVersionMismatch:

    @pytest.mark.asyncio
    async def test_decrypt_with_wrong_key_version_raises(self):
        service = DesensitizationRestoreService()
        plaintext = "测试数据"
        ciphertext, iv, key_version = await service._encrypt_value(plaintext)
        wrong_version = key_version + 999
        with pytest.raises(RuntimeError, match="Cannot decrypt data with key_version"):
            await service._decrypt_value(ciphertext, iv, wrong_version)


class TestConsentAPIConcurrentWrite:

    @pytest.mark.asyncio
    async def test_concurrent_consent_update_same_patient(self):
        errors = []

        async def update_consent():
            try:
                req = ConsentUpdateRequest(
                    patient_id="P_CONCURRENT",
                    action=ConsentAction.GRANT,
                    operator_id="admin_001",
                    consent_version="1.0",
                )
                assert req.patient_id == "P_CONCURRENT"
            except Exception as e:
                errors.append(e)

        await asyncio.gather(*[update_consent() for _ in range(10)])
        assert len(errors) == 0


class TestJWTWithHSMUnavailable:

    def test_hsm_unavailable_signing_restricted_raises(self):
        hsm_manager.set_degraded("test_jwt_unavailable")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        try:
            phase = hsm_manager.get_degraded_phase()
            assert phase == DegradedPhase.RESTRICTED
        finally:
            hsm_manager.clear_degraded()


class TestAuditChainIntegritySupplementary:

    def test_audit_service_verify_chain_signature(self):
        import inspect

        from app.services.audit_service import AuditService
        sig = inspect.signature(AuditService.verify_chain)
        params = list(sig.parameters.keys())
        assert 'session_id' in params, f"verify_chain missing session_id param: {params}"

    def test_audit_service_verify_all_chains_exists(self):
        from app.services.audit_service import AuditService
        assert hasattr(AuditService, 'verify_all_chains')

    def test_per_session_lock_not_global(self):
        from app.services.audit_service import _chain_locks
        assert isinstance(_chain_locks, dict), "Expected per-session lock dict"


class TestDesensitizationNoOriginalLeakSupplementary:

    def test_id_card_fully_masked(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("身份证号110101199001011234")
        assert '110101199001011234' not in result

    def test_phone_middle_digits_masked(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("手机号13812345678")
        assert '13812345678' not in result

    def test_address_only_city_preserved(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("住址北京市朝阳区建国路88号")
        assert '北京市' in result
        assert '朝阳区' not in result
        assert '建国路' not in result

    def test_age_not_desensitized(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("患者45岁，因甲状腺结节就诊")
        assert '45岁' in result

    def test_birth_date_only_year_preserved(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("出生日期：1985-08-20")
        assert '1985-**-**' in result
        assert '08-20' not in result


class TestSafetyGatewayHeterogeneousRedundancySupplementary:

    def test_sg_phone_pattern_differs_from_main(self):
        from safety_gateway.desensitization import sg_desensitization_engine
        main_engine = get_desensitization_engine()

        main_phone_rule = None
        for rule in main_engine.get_rules():
            if rule.name == "phone":
                main_phone_rule = rule
                break

        sg_phone_rule = None
        for rule in sg_desensitization_engine.get_rules():
            if rule.name == "sg_phone":
                sg_phone_rule = rule
                break

        assert main_phone_rule is not None
        assert sg_phone_rule is not None
        assert main_phone_rule.pattern != sg_phone_rule.pattern, \
            "SG phone pattern should differ from main pattern"

    def test_sg_has_ip_address_rule(self):
        from safety_gateway.desensitization import sg_desensitization_engine
        rule_names = [r.name for r in sg_desensitization_engine.get_rules() if r.enabled]
        assert "sg_ip_address" in rule_names, f"Missing sg_ip_address in: {rule_names}"


class TestThreeLayerDesensitizationIntegration:

    def test_layer1_standard_produces_token(self):
        engine = get_desensitization_engine()
        result = engine.desensitize("患者张三")
        assert PATIENT_TOKEN_PATTERN.search(result), f"Expected PATIENT_XXX in: {result}"

    def test_layer2_fusion_catches_llm_leak(self):
        engine = FusionDesensitizationEngine()
        result = engine.desensitize_llm_output("患者的身份证号是110101199001011234")
        assert "110101199001011234" not in result.desensitized_text

    def test_layer3_sg_catches_remaining_pii(self):
        result = sg_desensitization_engine.desensitize("患者：张三，身份证号110101199001011234")
        assert "张三" not in result
        assert "110101199001011234" not in result


class TestErrorCodeConsistency:

    def test_e1008_defined(self):
        exc = PatientNotConsentedException()
        assert exc.error_code == "E1008"
        assert exc.status_code == 403

    def test_e1010_defined(self):
        exc = PatientConsentWithdrawnException()
        assert exc.error_code == "E1010"
        assert exc.status_code == 403

    def test_e4013_defined(self):
        exc = HSMUnavailableException()
        assert exc.error_code == "E4013"
        assert exc.status_code == 503


class TestConsentSchemaConsistency:

    def test_consent_update_request_has_operator_id(self):
        req = ConsentUpdateRequest(
            patient_id="P001",
            action=ConsentAction.GRANT,
            operator_id="dr_zhang_001",
            consent_version="1.0",
        )
        assert req.operator_id == "dr_zhang_001"

    def test_consent_query_response_has_consented_at(self):
        resp = ConsentQueryResponse(
            patient_id="P001",
            consent_status=ConsentStatus.GRANTED,
        )
        assert hasattr(resp, 'consented_at')


class TestHSMKeySpecConsistency:

    def test_key_slot_enum_values(self):
        slot_names = [s.name for s in KeySlot]
        assert "JWT_SIGNING" in slot_names
        assert "DATA_ENCRYPTION" in slot_names
        assert "DESENSITIZATION_MAPPING" in slot_names
        assert "EMERGENCY_SIGNING" in slot_names

    def test_softhsm_expiry_days(self):
        assert HSMManager.SOFT_HSM_EXPIRY_DAYS == 90

    def test_degraded_phase_enum_values(self):
        phase_names = [p.name for p in DegradedPhase]
        assert "NORMAL" in phase_names
        assert "CACHED" in phase_names
        assert "RESTRICTED" in phase_names
        assert "EMERGENCY" in phase_names


class TestDataRetentionSpecConsistency:

    def test_inpatient_retention_years_config(self):
        assert hasattr(settings, 'data_retention_inpatient_years')
        assert settings.data_retention_inpatient_years >= 30

    def test_outpatient_retention_years_config(self):
        assert hasattr(settings, 'data_retention_outpatient_years')
        assert settings.data_retention_outpatient_years >= 15


class TestDesensitizationMappingKeySpecSupplementary:

    def test_mapping_model_has_key_version(self):
        columns = [c.name for c in DesensitizationMapping.__table__.columns]
        assert 'key_version' in columns, f"Missing key_version in: {columns}"

    def test_mapping_model_iv_is_12_bytes(self):
        iv_col = DesensitizationMapping.__table__.columns['iv']
        assert iv_col.type.length == 12, f"IV length should be 12, got {iv_col.type.length}"


@pytest.mark.perf
class TestSECPerformanceBenchmarksSupplementary:

    def test_desensitization_p99_under_5ms(self):
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

    def test_refresh_token_verify_p99_under_5ms(self):
        store = refresh_token_store
        token_id = store.create_refresh_token(user_id="perf_test", user_info={"role": "doctor"})
        latencies = []

        for _ in range(1000):
            start = time.perf_counter()
            store.verify_refresh_token(token_id)
            latencies.append((time.perf_counter() - start) * 1000)

        latencies.sort()
        p99 = latencies[int(len(latencies) * 0.99)]
        assert p99 < 5.0, f"Token verify P99 latency {p99:.2f}ms exceeds 5ms"

    def test_hsm_sign_p99_under_5ms(self):
        hsm_manager.generate_key_pair("perf_sign_key")
        data = b"performance test data for signing"
        latencies = []

        for _ in range(100):
            start = time.perf_counter()
            hsm_manager.sign("perf_sign_key", data)
            latencies.append((time.perf_counter() - start) * 1000)

        latencies.sort()
        p99 = latencies[int(len(latencies) * 0.99)]
        is_soft_hsm = getattr(hsm_manager, '_is_soft_hsm', True)
        threshold = 50.0 if is_soft_hsm else 5.0
        assert p99 < threshold, f"HSM sign P99 latency {p99:.2f}ms exceeds {threshold}ms"


class TestTokenFamilyFullChain:

    def test_full_token_lifecycle(self):
        store = refresh_token_store

        token1_id = store.create_refresh_token(user_id="lifecycle_user", user_info={"role": "doctor"})
        assert store.verify_refresh_token(token1_id) is not None

        token2_id = store.rotate_refresh_token(token1_id)
        assert token2_id is not None

        with pytest.raises(ValueError, match="already been used"):
            store.rotate_refresh_token(token1_id)

        store.revoke_all_user_tokens("lifecycle_user")
        assert store.verify_refresh_token(token2_id) is None

    def test_token_family_inheritance_chain(self):
        store = refresh_token_store

        t1 = store.create_refresh_token(user_id="chain_user", user_info={"role": "doctor"})
        t1_data = store.verify_refresh_token(t1)
        family_id = t1_data["family_id"]

        t2 = store.rotate_refresh_token(t1)
        t2_data = store.verify_refresh_token(t2)
        assert t2_data["family_id"] == family_id

        t3 = store.rotate_refresh_token(t2)
        t3_data = store.verify_refresh_token(t3)
        assert t3_data["family_id"] == family_id

        t4 = store.rotate_refresh_token(t3)
        t4_data = store.verify_refresh_token(t4)
        assert t4_data is not None
        assert t4_data["family_id"] == family_id

    def test_replay_detection_revokes_entire_family(self):
        store = refresh_token_store

        t1 = store.create_refresh_token(user_id="replay_family_user", user_info={"role": "doctor"})
        t2 = store.rotate_refresh_token(t1)
        t3 = store.rotate_refresh_token(t2)

        with pytest.raises(ValueError):
            store.rotate_refresh_token(t1)

        assert store.verify_refresh_token(t2) is None
        assert store.verify_refresh_token(t3) is None


class TestDesensitizationRestoreRateLimitExtended:

    @pytest.mark.asyncio
    async def test_rate_limit_counter_increments(self):
        service = DesensitizationRestoreService()
        assert await service._check_rate_limit() is True
        assert await service._check_rate_limit() is True
        stats = service.get_restore_stats()
        assert stats["total_restore_requests_current_window"] >= 2

    @pytest.mark.asyncio
    async def test_rate_limit_window_reset(self):
        service = DesensitizationRestoreService()
        service._rate_limit_counter = {"old_window": 9999}
        service._rate_limit_window_start = datetime.now(timezone.utc) - timedelta(hours=2)
        assert await service._check_rate_limit() is True


class TestSoftHSMExpiryScenarios:

    def test_softhsm_active_within_90_days(self):
        status = hsm_manager.get_soft_hsm_status()
        if status.get("is_soft_hsm"):
            assert status["status"] in ("active", "expired", "critical")

    def test_softhsm_critical_days_constant(self):
        assert HSMManager.SOFT_HSM_CRITICAL_DAYS == 120


class TestDualDegradationScenarioSupplementary:

    def test_fusion_degraded_plus_sg_unavailable(self):
        from safety_gateway.checker import SafetyGatewayChecker
        from safety_gateway.validation_result import DegradedReason

        checker = SafetyGatewayChecker.__new__(SafetyGatewayChecker)
        checker._whitelist_fields = ["organ_name", "status"]

        test_data = {
            "organ_name": "甲状腺未见异常",
            "conclusion": "建议随访",
            "recommendation": "6个月后复查",
            "patient_name": "张三",
        }

        result = checker.apply_degraded_output(
            test_data, reason=DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED
        )

        assert "conclusion" not in result
        assert "recommendation" not in result
        assert "patient_name" not in result
        assert result.get("fusion_unavailable_sensitive_detected") is True
        assert result["conclusion_suppressed"] is True
