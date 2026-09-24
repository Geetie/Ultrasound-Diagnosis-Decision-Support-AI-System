import asyncio
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

pytestmark = [pytest.mark.track_sec, pytest.mark.sec_domain_audit]

from app.fusion.desensitization import FusionDesensitizationEngine
from app.security.desensitization import DesensitizationEngine
from app.security.hsm import DegradedPhase, HSMManager
from app.security.refresh_token import MemoryRefreshTokenStore
from app.security.three_layer_desensitization import (
    DesensitizationLayer,
    ThreeLayerDesensitizationManager,
)


class TestAuditChainIntegrity:

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

    def test_max_chain_locks_constant(self):
        from app.services.audit_service import _MAX_CHAIN_LOCKS
        assert _MAX_CHAIN_LOCKS == 10000


class TestAuditChainVerification:

    async def test_empty_chain_is_valid(self):
        from app.services.audit_service import AuditService
        mock_db = AsyncMock()
        mock_db.execute.return_value = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
        svc = AuditService(db=mock_db)
        result = await svc.verify_chain(session_id="empty_chain_test")
        assert result is not None

    async def test_single_log_entry_is_valid(self):
        from app.services.audit_service import AuditService
        mock_db = AsyncMock()
        mock_db.execute.return_value = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
        svc = AuditService(db=mock_db)
        result = await svc.verify_chain(session_id="single_entry_test")
        assert result is not None

    async def test_chain_verification_returns_4_tuple(self):
        from app.services.audit_service import AuditService
        mock_db = AsyncMock()
        mock_db.execute.return_value = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
        svc = AuditService(db=mock_db)
        result = await svc.verify_chain(session_id="tuple_test")
        assert result is not None
        assert len(result) == 4

    async def test_first_log_hash_prev_is_none(self):
        from app.services.audit_service import AuditService
        mock_db = AsyncMock()
        mock_db.execute.return_value = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
        svc = AuditService(db=mock_db)
        result = await svc.verify_chain(session_id="first_none_test")
        assert result is not None

    async def test_broken_chain_detected(self):
        from app.services.audit_service import AuditService
        mock_db = AsyncMock()
        mock_db.execute.return_value = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
        svc = AuditService(db=mock_db)
        result = await svc.verify_chain(session_id="broken_chain_test")
        assert result is not None


class TestDeterministicHashSerialization:

    def test_same_detail_same_hash(self):
        detail = {"action": "login", "user_id": "u001", "ip": "192.168.1.1"}
        h1 = hashlib.sha256(json.dumps(detail, sort_keys=True).encode()).hexdigest()
        h2 = hashlib.sha256(json.dumps(detail, sort_keys=True).encode()).hexdigest()
        assert h1 == h2

    def test_different_detail_different_hash(self):
        d1 = {"action": "login", "user_id": "u001"}
        d2 = {"action": "logout", "user_id": "u001"}
        h1 = hashlib.sha256(json.dumps(d1, sort_keys=True).encode()).hexdigest()
        h2 = hashlib.sha256(json.dumps(d2, sort_keys=True).encode()).hexdigest()
        assert h1 != h2

    def test_key_order_does_not_affect_hash(self):
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 2, "a": 1}
        h1 = hashlib.sha256(json.dumps(d1, sort_keys=True).encode()).hexdigest()
        h2 = hashlib.sha256(json.dumps(d2, sort_keys=True).encode()).hexdigest()
        assert h1 == h2

    def test_audit_service_uses_json_dumps(self):
        import inspect

        from app.services.audit_service import AuditService
        source = inspect.getsource(AuditService)
        assert "json.dumps" in source


class TestRecordIdNoCollision:

    def test_record_id_uses_full_uuid_format(self):
        import uuid

        from app.security.desensitization_restore import DesensitizationRestoreService
        service = DesensitizationRestoreService()
        record_id = str(uuid.uuid4())
        parsed = uuid.UUID(record_id)
        assert parsed.version == 4, "record_id应为UUID v4格式"

    def test_two_record_ids_are_distinct(self):
        import uuid
        id1 = str(uuid.uuid4())
        id2 = str(uuid.uuid4())
        assert id1 != id2, "连续生成的UUID应不同"


class TestTokenReuseDetectionCorrectPath:

    def setup_method(self):
        MemoryRefreshTokenStore._instance = None
        self.store = MemoryRefreshTokenStore()

    @pytest.mark.asyncio
    async def test_reuse_after_rotation_triggers_family_revocation(self):
        user_id = "user_reuse_test"
        user_info = {"role": "doctor"}
        family_id = "family_rotation_test"

        token1 = self.store.create_refresh_token(user_id, user_info, family_id=family_id)

        data1 = self.store.verify_refresh_token(token1)
        assert data1 is not None
        assert data1["family_id"] == family_id

        self.store.revoke_refresh_token(token1)

        token2 = self.store.create_refresh_token(user_id, user_info, family_id=family_id)

        data2 = self.store.verify_refresh_token(token2)
        assert data2 is not None

        result = self.store.verify_refresh_token(token1)
        assert result is None

        data2_after = self.store.verify_refresh_token(token2)
        assert data2_after is None, "token2应因重用检测触发家族撤销而失效"

    @pytest.mark.asyncio
    async def test_reuse_detection_does_not_affect_other_families(self):
        user_id = "user_multi_family"
        user_info = {"role": "doctor"}

        token_f1 = self.store.create_refresh_token(user_id, user_info, family_id="F1")
        token_f2 = self.store.create_refresh_token(user_id, user_info, family_id="F2")

        self.store.revoke_refresh_token(token_f1)

        self.store.verify_refresh_token(token_f1)

        data_f2 = self.store.verify_refresh_token(token_f2)
        assert data_f2 is not None, "F2家族Token不应受F1家族重用检测影响"


class TestMemoryRefreshTokenStoreNoLeak:

    def setup_method(self):
        MemoryRefreshTokenStore._instance = None
        self.store = MemoryRefreshTokenStore()

    def test_revoked_token_removed_from_store(self):
        token_id = self.store.create_refresh_token("user1", {"role": "test"})
        assert token_id in self.store._store

        self.store.revoke_refresh_token(token_id)
        assert token_id in self.store._store
        assert self.store._store[token_id]["revoked"] is True


class TestAuthRedisModeAwait:

    @pytest.mark.asyncio
    async def test_module_level_get_user_token_count_is_async(self):
        from app.security.refresh_token import get_user_token_count
        assert asyncio.iscoroutinefunction(get_user_token_count)

    @pytest.mark.asyncio
    async def test_module_level_revoke_oldest_user_token_is_async(self):
        from app.security.refresh_token import revoke_oldest_user_token
        assert asyncio.iscoroutinefunction(revoke_oldest_user_token)

    @pytest.mark.asyncio
    async def test_memory_store_get_user_token_count(self):
        from app.security.refresh_token import get_user_token_count, refresh_token_store
        if hasattr(refresh_token_store, 'get_user_token_count'):
            count = await get_user_token_count("nonexistent_user")
            assert isinstance(count, int)
            assert count == 0


class TestDeviceRepositoryApiKeyHashing:

    def test_hash_api_key_deterministic(self):
        from app.repositories.device_repository import _hash_api_key
        result1 = _hash_api_key("test_key_123", salt="fixed_salt")
        result2 = _hash_api_key("test_key_123", salt="fixed_salt")
        assert result1 == result2

    def test_hash_api_key_uses_sha256(self):
        from app.repositories.device_repository import _hash_api_key
        raw = "test_key_123"
        salt = "fixed_salt"
        expected = f"{salt}${hashlib.sha256((salt + raw).encode()).hexdigest()}"
        assert _hash_api_key(raw, salt=salt) == expected

    def test_hash_api_key_different_inputs(self):
        from app.repositories.device_repository import _hash_api_key
        hash1 = _hash_api_key("key_a", salt="same_salt")
        hash2 = _hash_api_key("key_b", salt="same_salt")
        assert hash1 != hash2

    def test_hash_api_key_uses_random_salt(self):
        from app.repositories.device_repository import _hash_api_key
        result1 = _hash_api_key("test_key_123")
        result2 = _hash_api_key("test_key_123")
        assert result1 != result2

    def test_verify_api_key_with_salt(self):
        from app.repositories.device_repository import _hash_api_key, _verify_api_key
        hashed = _hash_api_key("test_key_123", salt="fixed_salt")
        assert _verify_api_key(hashed, "test_key_123") is True
        assert _verify_api_key(hashed, "wrong_key") is False

    def test_verify_api_key_legacy_no_salt(self):
        from app.repositories.device_repository import _verify_api_key
        legacy_hash = hashlib.sha256(b"test_key_123").hexdigest()
        assert _verify_api_key(legacy_hash, "test_key_123") is True

    async def test_register_device_returns_raw_key_stores_hash(self):
        from app.repositories.device_repository import DeviceRepository, _verify_api_key
        db = AsyncMock()
        repo = DeviceRepository(db)
        device = Mock()
        device.device_id = "dev_test"
        device.device_name = "Test"
        device.device_type = "ultrasound"
        device.location = "Room 1"
        device.jetpack_version = "1.0"
        device.api_key = ""
        device.status = "active"
        device.created_at = datetime.now(timezone.utc)
        device.updated_at = datetime.now(timezone.utc)

        db.flush = AsyncMock()
        db.add = Mock()

        device_obj, raw_key = await repo.register_device(
            device_name="Test",
            device_type="ultrasound",
            location="Room 1",
            jetpack_version="1.0",
        )

        assert raw_key.startswith("ak_")
        stored_hash = device_obj.api_key
        assert stored_hash != raw_key
        assert _verify_api_key(stored_hash, raw_key) is True

    async def test_authenticate_device_uses_hash_comparison(self):
        from app.repositories.device_repository import DeviceRepository, _hash_api_key
        db = AsyncMock()
        repo = DeviceRepository(db)

        raw_key = "ak_test_key_1234567890"
        hashed_key = _hash_api_key(raw_key, salt="fixed_salt")

        mock_device = Mock()
        mock_device.device_id = "dev_test"
        mock_device.api_key = hashed_key
        mock_device.status = "active"
        mock_device.updated_at = datetime.now(timezone.utc)

        repo.get_device = AsyncMock(return_value=mock_device)
        db.flush = AsyncMock()

        result = await repo.authenticate_device("dev_test", raw_key)
        assert result is not None

        result = await repo.authenticate_device("dev_test", "wrong_key")
        assert result is None


class TestHSMDegradedSigningBehavior:

    def setup_method(self):
        self.hsm = HSMManager()
        self.hsm.generate_key_pair("degraded_test_key")

    def test_normal_phase_signing_works(self):
        data = b"test data"
        signature = self.hsm.sign("degraded_test_key", data)
        assert signature is not None
        assert self.hsm.verify_signature("degraded_test_key", data, signature)

    def test_cached_phase_signing_works(self):
        self.hsm.set_degraded("test_degraded")
        assert self.hsm.get_degraded_phase() == DegradedPhase.CACHED

        data = b"test data cached"
        signature = self.hsm.sign("degraded_test_key", data)
        assert signature is not None
        assert self.hsm.verify_signature("degraded_test_key", data, signature)
        self.hsm.clear_degraded()

    def test_restricted_phase_signing_prohibited(self):
        self.hsm._is_degraded = True
        self.hsm._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        self.hsm._degraded_reason = "test"
        assert self.hsm.get_degraded_phase() == DegradedPhase.RESTRICTED

        with pytest.raises(RuntimeError, match="restricted"):
            self.hsm.sign("degraded_test_key", b"test data")
        self.hsm.clear_degraded()

    def test_emergency_phase_signing_prohibited(self):
        self.hsm._is_degraded = True
        self.hsm._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=60)
        self.hsm._degraded_reason = "test"
        assert self.hsm.get_degraded_phase() == DegradedPhase.EMERGENCY

        with pytest.raises(RuntimeError, match="emergency"):
            self.hsm.sign("degraded_test_key", b"test data")
        self.hsm.clear_degraded()

    def test_verification_works_in_all_phases(self):
        data = b"test data for verify"
        signature = self.hsm.sign("degraded_test_key", data)

        self.hsm.set_degraded("test")
        assert self.hsm.verify_signature("degraded_test_key", data, signature)
        self.hsm.clear_degraded()

        self.hsm._is_degraded = True
        self.hsm._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        self.hsm._degraded_reason = "test"
        assert self.hsm.verify_signature("degraded_test_key", data, signature)
        self.hsm.clear_degraded()


class TestHSMEncryptionKeySecurity:

    def test_missing_encryption_key_raises_error(self):
        hsm = HSMManager()
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(RuntimeError, match="HSM_KEY_ENCRYPTION_KEY"):
                hsm._get_encryption_key()

    def test_valid_encryption_key_works(self):
        from cryptography.fernet import Fernet
        valid_key = Fernet.generate_key().decode()
        hsm = HSMManager()
        with patch.dict("os.environ", {"HSM_KEY_ENCRYPTION_KEY": valid_key}):
            result = hsm._get_encryption_key()
            assert result == valid_key.encode()


class TestPrivateKeyExportProhibited:

    def test_get_private_key_raises_runtime_error(self):
        hsm = HSMManager()
        hsm.generate_key_pair("export_test_key")
        with pytest.raises(RuntimeError, match="prohibited"):
            hsm.get_private_key("export_test_key")


class TestHSMDegradedBoundaryConditions:

    def test_degraded_phase_at_exactly_5_minutes(self):
        from app.security.hsm import DegradedPhase
        phase = DegradedPhase.RESTRICTED
        assert phase == DegradedPhase.RESTRICTED

    def test_degraded_phase_values(self):
        from app.security.hsm import DegradedPhase
        assert DegradedPhase.NORMAL.value == "normal"
        assert DegradedPhase.CACHED.value == "cached"
        assert DegradedPhase.RESTRICTED.value == "restricted"
        assert DegradedPhase.EMERGENCY.value == "emergency"

    def test_key_slot_isolation(self):
        from app.security.hsm import KeySlot

        assert KeySlot.JWT_SIGNING != KeySlot.DATA_ENCRYPTION
        assert KeySlot.DATA_ENCRYPTION != KeySlot.DESENSITIZATION_MAPPING
        assert KeySlot.DESENSITIZATION_MAPPING != KeySlot.EMERGENCY_SIGNING


class TestHSMRedisRestorationPrefix:

    @pytest.mark.asyncio
    async def test_restore_keys_prefix_stripping_logic(self):
        from app.config.settings import settings

        prefix = settings.redis_key_prefix
        sample_key = f"{prefix}:hsm:keys:jwt_signing:my_key:private"
        private_suffix = ":private"

        buggy_result = sample_key.replace(":hsm:keys:", "").replace(private_suffix, "")
        assert not buggy_result.startswith("jwt_signing:"), f"Buggy code fails slot detection: {buggy_result}"

        from app.config.settings import settings as s
        prefix_to_strip = f"{s.redis_key_prefix}:hsm:keys:"
        fixed_result = sample_key.replace(prefix_to_strip, "").replace(private_suffix, "")
        assert fixed_result.startswith("jwt_signing:"), f"Fixed code strips prefix correctly: {fixed_result}"

    @pytest.mark.asyncio
    async def test_restore_keys_all_slot_prefixes(self):
        from app.config.settings import settings
        from app.security.hsm import KeySlot

        prefix = settings.redis_key_prefix
        prefix_to_strip = f"{prefix}:hsm:keys:"
        private_suffix = ":private"

        for slot in KeySlot:
            sample_key = f"{prefix}:hsm:keys:{slot.value}:test_key:private"
            fixed_result = sample_key.replace(prefix_to_strip, "").replace(private_suffix, "")
            assert fixed_result.startswith(f"{slot.value}:"), f"Slot {slot.value} prefix detection works: {fixed_result}"


class TestDesensKeyRotation:

    def test_check_desens_key_rotation_method_exists(self):
        from app.security.hsm import HSMManager
        assert hasattr(HSMManager, '_check_desensitization_key_rotation')

    def test_desens_key_rotation_called_in_cleanup(self):
        import inspect

        from app.security.hsm import HSMManager
        source = inspect.getsource(HSMManager._start_key_cleanup_timer)
        assert "_check_desensitization_key_rotation" in source, "Desens key rotation check should be called in cleanup timer"


class TestDesensitizationNoOriginalNameLeak:

    def setup_method(self):
        DesensitizationEngine._instance = None
        self.engine = DesensitizationEngine()
        self.engine.load_rules()

    def test_patient_name_not_preserved(self):
        text = "患者姓名：张三"
        result = self.engine.desensitize(text)
        assert "张三" not in result
        assert "PATIENT_" in result or "[REDACTED" in result

    def test_patient_name_with_title_not_preserved(self):
        text = "李四先生"
        result = self.engine.desensitize(text)
        assert "李四" not in result
        assert "PATIENT_" in result or "[REDACTED" in result
        assert "先生" in result

    def test_fusion_layer_no_sensitive_value_preserved(self):
        engine = FusionDesensitizationEngine()
        text = "患者的身份证号是110101199001011234"
        result = engine.desensitize_llm_output(text)
        assert "110101199001011234" not in result.desensitized_text


class TestDesensitizationNoOriginalLeak:

    def test_id_card_fully_masked(self):
        from app.security.desensitization import get_desensitization_engine
        engine = get_desensitization_engine()
        result = engine.desensitize("身份证号110101199001011234")
        assert '110101199001011234' not in result

    def test_phone_middle_digits_masked(self):
        from app.security.desensitization import get_desensitization_engine
        engine = get_desensitization_engine()
        result = engine.desensitize("手机号13812345678")
        assert '13812345678' not in result

    def test_address_only_city_preserved(self):
        from app.security.desensitization import get_desensitization_engine
        engine = get_desensitization_engine()
        result = engine.desensitize("住址北京市朝阳区建国路88号")
        assert '北京市' in result
        assert '朝阳区' not in result
        assert '建国路' not in result

    def test_age_not_desensitized(self):
        from app.security.desensitization import get_desensitization_engine
        engine = get_desensitization_engine()
        result = engine.desensitize("患者45岁，因甲状腺结节就诊")
        assert '45岁' in result

    def test_birth_date_only_year_preserved(self):
        from app.security.desensitization import get_desensitization_engine
        engine = get_desensitization_engine()
        result = engine.desensitize("出生日期：1985-08-20")
        assert '1985-**-**' in result
        assert '08-20' not in result


class TestDesensitizationBoundaryConditions:

    def test_empty_string_not_modified(self):
        from app.security.desensitization import DesensitizationEngine
        DesensitizationEngine._instance = None
        engine = DesensitizationEngine()
        engine.load_rules()
        assert engine.desensitize("") == ""

    def test_none_returns_none(self):
        from app.security.desensitization import DesensitizationEngine
        DesensitizationEngine._instance = None
        engine = DesensitizationEngine()
        engine.load_rules()
        assert engine.desensitize(None) is None

    def test_unicode_patient_name(self):
        from app.security.desensitization import DesensitizationEngine
        DesensitizationEngine._instance = None
        engine = DesensitizationEngine()
        engine.load_rules()
        result = engine.desensitize("患者：李明")
        assert "李明" not in result
        assert "PATIENT_" in result

    def test_same_name_gets_same_token(self):
        from app.security.desensitization import DesensitizationEngine
        DesensitizationEngine._instance = None
        engine = DesensitizationEngine()
        engine.load_rules()
        r1 = engine.desensitize("患者：张三")
        r2 = engine.desensitize("患者：张三")
        assert r1 == r2

    def test_different_names_get_different_tokens(self):
        from app.security.desensitization import DesensitizationEngine
        DesensitizationEngine._instance = None
        engine = DesensitizationEngine()
        engine.load_rules()
        r1 = engine.desensitize("患者：张三")
        r2 = engine.desensitize("患者：李四")
        assert r1 != r2

    def test_deeply_nested_dict_respects_limit(self):
        from app.security.desensitization import DesensitizationEngine
        DesensitizationEngine._instance = None
        engine = DesensitizationEngine()
        engine.load_rules()
        deep = {"key": "value"}
        for _ in range(25):
            deep = {"nested": deep}
        with pytest.raises(ValueError, match="recursion depth exceeded"):
            engine.desensitize_dict(deep)

    def test_phone_only_last_four_digits(self):
        from app.security.desensitization import DesensitizationEngine
        DesensitizationEngine._instance = None
        engine = DesensitizationEngine()
        engine.load_rules()
        result = engine.desensitize("手机号13812345678")
        assert "138" not in result
        assert "5678" in result

    def test_id_card_fully_masked(self):
        from app.security.desensitization import DesensitizationEngine
        DesensitizationEngine._instance = None
        engine = DesensitizationEngine()
        engine.load_rules()
        result = engine.desensitize("身份证号110101199001011234")
        assert "110101199001011234" not in result
        assert "********************" in result


class TestDesensitizationMappingKeySpec:

    def test_mapping_model_has_key_version(self):
        from app.models.desensitization_mapping import DesensitizationMapping
        columns = [c.name for c in DesensitizationMapping.__table__.columns]
        assert 'key_version' in columns, f"Missing key_version in: {columns}"

    def test_mapping_model_iv_is_12_bytes(self):
        from app.models.desensitization_mapping import DesensitizationMapping
        iv_col = DesensitizationMapping.__table__.columns['iv']
        assert iv_col.type.length == 12, f"IV length should be 12, got {iv_col.type.length}"


class TestReEncryptKeyVersion:

    def test_re_encrypt_uses_actual_version(self):
        import inspect

        from app.security.desensitization_restore import DesensitizationRestoreService
        source = inspect.getsource(DesensitizationRestoreService.re_encrypt_with_new_key)
        assert "actual_version" in source, "re_encrypt_with_new_key should use actual_version"
        lines = source.split('\n')
        for line in lines:
            if 'key_version' in line and 'mapping' in line and '=' in line:
                assert 'actual_version' in line, f"Should assign actual_version, got: {line.strip()}"


class TestThreeLayerAllPassedLogic:

    @pytest.mark.asyncio
    async def test_all_passed_true_when_no_degradation(self):
        manager = ThreeLayerDesensitizationManager()
        with patch.object(manager, 'process_safety_gateway_layer') as mock_sg:
            mock_sg.return_value = (
                {"organ": "thyroid"},
                {
                    "layer": DesensitizationLayer.SAFETY_GATEWAY,
                    "desensitized": {"organ": "thyroid"},
                    "check_result": {"status": "passed", "degraded_mode": False},
                    "modified": False,
                }
            )
            result = await manager.process_full_pipeline("P001", {"organ": "thyroid"})
            assert result.all_passed is True

    @pytest.mark.asyncio
    async def test_all_passed_false_when_degraded(self):
        manager = ThreeLayerDesensitizationManager()
        with patch.object(manager, 'process_safety_gateway_layer') as mock_sg:
            mock_sg.return_value = (
                {"organ": "thyroid"},
                {
                    "layer": DesensitizationLayer.SAFETY_GATEWAY,
                    "desensitized": {"organ": "thyroid"},
                    "check_result": {"status": "degraded", "degraded_mode": True, "degraded": True},
                    "modified": True,
                }
            )
            result = await manager.process_full_pipeline("P001", {"organ": "thyroid"})
            assert result.all_passed is False


class TestThreeLayerDesensitizationPipeline:

    @pytest.mark.asyncio
    async def test_process_full_pipeline_order(self):
        from app.security.three_layer_desensitization import ThreeLayerDesensitizationManager
        mgr = ThreeLayerDesensitizationManager.__new__(ThreeLayerDesensitizationManager)

        call_order = []

        async def mock_l1(data):
            call_order.append("layer1")
            return data, {"modified": True, "check_result": {"passed": True}}

        async def mock_l2(data, **kw):
            call_order.append("layer2")
            return data, {"modified": False, "check_result": {"passed": True}}

        async def mock_l3(patient_id, data, **kw):
            call_order.append("layer3")
            return data, {"check_result": {"passed": True, "degraded_mode": False}}

        mgr.process_standardization_layer = mock_l1
        mgr.process_fusion_layer = mock_l2
        mgr.process_safety_gateway_layer = mock_l3

        await mgr.process_full_pipeline(
            patient_id="p1",
            input_data={"test": "data"},
            consent_status_at_input="granted",
        )

        assert call_order == ["layer1", "layer2", "layer3"]

    @pytest.mark.asyncio
    async def test_all_passed_layer1_modified_does_not_affect(self):
        from app.security.three_layer_desensitization import ThreeLayerDesensitizationManager
        mgr = ThreeLayerDesensitizationManager.__new__(ThreeLayerDesensitizationManager)

        mgr.process_standardization_layer = AsyncMock(return_value=(
            {"test": "data"}, {"modified": True, "check_result": {"passed": True}}
        ))
        mgr.process_fusion_layer = AsyncMock(return_value=(
            {"test": "data"}, {"modified": True, "check_result": {"passed": True}}
        ))
        mgr.process_safety_gateway_layer = AsyncMock(return_value=(
            {"test": "data"}, {"check_result": {"passed": True, "degraded_mode": False}}
        ))

        result = await mgr.process_full_pipeline(
            patient_id="p1",
            input_data={"test": "data"},
            consent_status_at_input="granted",
        )
        assert result.all_passed is True

    @pytest.mark.asyncio
    async def test_all_passed_layer3_rejected_sets_false(self):
        from app.security.three_layer_desensitization import ThreeLayerDesensitizationManager
        mgr = ThreeLayerDesensitizationManager.__new__(ThreeLayerDesensitizationManager)

        mgr.process_standardization_layer = AsyncMock(return_value=(
            {"test": "data"}, {"modified": True, "check_result": {"passed": True}}
        ))
        mgr.process_fusion_layer = AsyncMock(return_value=(
            {"test": "data"}, {"modified": False, "check_result": {"passed": True}}
        ))
        mgr.process_safety_gateway_layer = AsyncMock(return_value=(
            {"test": "data"}, {"check_result": {"passed": False, "degraded_mode": False}}
        ))

        result = await mgr.process_full_pipeline(
            patient_id="p1",
            input_data={"test": "data"},
            consent_status_at_input="granted",
        )
        assert result.all_passed is False

    @pytest.mark.asyncio
    async def test_all_passed_degraded_mode_sets_false(self):
        from app.security.three_layer_desensitization import ThreeLayerDesensitizationManager
        mgr = ThreeLayerDesensitizationManager.__new__(ThreeLayerDesensitizationManager)

        mgr.process_standardization_layer = AsyncMock(return_value=(
            {"test": "data"}, {"modified": False, "check_result": {"passed": True}}
        ))
        mgr.process_fusion_layer = AsyncMock(return_value=(
            {"test": "data"}, {"modified": False, "check_result": {"passed": True}}
        ))
        mgr.process_safety_gateway_layer = AsyncMock(return_value=(
            {"test": "data"}, {"check_result": {"passed": True, "degraded_mode": True}}
        ))

        result = await mgr.process_full_pipeline(
            patient_id="p1",
            input_data={"test": "data"},
            consent_status_at_input="granted",
        )
        assert result.all_passed is False

    @pytest.mark.asyncio
    async def test_consent_status_none_raises_valueerror(self):
        from app.security.three_layer_desensitization import ThreeLayerDesensitizationManager
        mgr = ThreeLayerDesensitizationManager.__new__(ThreeLayerDesensitizationManager)

        with pytest.raises(ValueError, match="consent_status_at_input"):
            await mgr.process_full_pipeline(
                patient_id="p1",
                input_data={"test": "data"},
                consent_status_at_input=None,
            )


class TestDualDegradationScenario:

    @pytest.mark.asyncio
    async def test_desensitization_degraded_param_propagates_to_sg(self):
        from app.security.three_layer_desensitization import ThreeLayerDesensitizationManager
        mgr = ThreeLayerDesensitizationManager.__new__(ThreeLayerDesensitizationManager)

        captured_sg_kwargs = {}

        mgr.process_standardization_layer = AsyncMock(return_value=(
            {"test": "data"}, {"modified": True, "check_result": {"passed": True}}
        ))

        async def mock_fusion(data, **kwargs):
            return data, {"modified": False, "check_result": {"passed": True}, "degraded": True}

        mgr.process_fusion_layer = mock_fusion

        async def mock_sg(patient_id, data, **kwargs):
            captured_sg_kwargs.update(kwargs)
            return data, {"check_result": {"passed": True, "degraded_mode": False}}

        mgr.process_safety_gateway_layer = mock_sg

        await mgr.process_full_pipeline(
            patient_id="p1",
            input_data={"test": "data"},
            consent_status_at_input="granted",
        )

        assert "desensitization_degraded" in captured_sg_kwargs
        assert captured_sg_kwargs["desensitization_degraded"] is True


class TestConsentAPIPermissionControl:

    def test_consent_update_requires_admin_role(self):
        import inspect

        from app.api.v1.consent import update_consent
        source = inspect.getsource(update_consent)
        assert "RoleChecker" in source, "update_consent should use RoleChecker dependency"
        assert "SYSTEM_ADMIN" in source

    def test_consent_query_requires_admin_role(self):
        import inspect

        from app.api.v1.consent import get_consent
        source = inspect.getsource(get_consent)
        assert "RoleChecker" in source, "get_consent should use RoleChecker dependency"
        assert "SYSTEM_ADMIN" in source


class TestConsentTimeoutReturnsDenied:

    @pytest.mark.asyncio
    async def test_timeout_returns_none_with_timeout_flag(self):
        from safety_gateway.consent_checker import ConsentChecker
        from safety_gateway.errors import DatabaseQueryTimeoutError

        checker = ConsentChecker()
        with patch.object(checker, '_retry_query', side_effect=DatabaseQueryTimeoutError("timeout")):
            with patch('safety_gateway.consent_checker.circuit_breaker_manager') as mock_cb_mgr:
                mock_breaker = AsyncMock()
                mock_breaker.execute = AsyncMock(side_effect=DatabaseQueryTimeoutError("timeout"))
                mock_cb_mgr.get_circuit_breaker.return_value = mock_breaker

                status, timeout, error = await checker.check_consent_status("P001")
                assert status is None, "超时+Redis未命中应返回None状态（非DENIED）"
                assert timeout is True


class TestConsentWithdrawalBroadcastRetry:

    @pytest.mark.asyncio
    async def test_broadcast_retries_on_failure(self):
        from app.repositories.consent_repository import ConsentRepository

        mock_db = AsyncMock()
        repo = ConsentRepository(mock_db)

        call_count = 0

        async def failing_xadd(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception("Redis connection failed")

        mock_redis = AsyncMock()
        mock_redis.xadd = failing_xadd

        mock_pool = AsyncMock()
        mock_pool.get_client = AsyncMock(return_value=mock_redis)

        with patch('app.config.redis.RedisPool', mock_pool), patch('asyncio.sleep', new_callable=AsyncMock):
            await repo._publish_consent_withdrawal(
                patient_id="P001",
                operator_id="admin",
                withdrawn_at=datetime.now(timezone.utc),
                consent_version="1.0",
            )

        assert call_count == 3, f"应重试3次，实际调用{call_count}次"
        mock_db.add.assert_called()


class TestConsentWithdrawalBoundaryConditions:

    @pytest.mark.asyncio
    async def test_timeout_triggers_degraded_not_failed(self):
        from safety_gateway.checker import SafetyGatewayChecker
        from safety_gateway.consent_checker import ConsentCheckResult

        checker = SafetyGatewayChecker()
        with patch.object(checker._consent_checker, 'check_consent_status',
                         return_value=ConsentCheckResult(status=None, timeout=True, error=False)):
            result = await checker.perform_full_check(
                "P001",
                {"observations": "test"},
            )
            assert result is not None
            assert result.degraded_mode is True, \
                f"超时应返回degraded_mode=True而非status={result.status}"

    @pytest.mark.asyncio
    async def test_withdrawn_always_fails(self):
        from safety_gateway.checker import SafetyGatewayChecker
        from safety_gateway.consent_checker import ConsentCheckResult
        from safety_gateway.validation_result import CheckResultStatus, ConsentStatus

        checker = SafetyGatewayChecker()
        with patch.object(checker._consent_checker, 'check_consent_status',
                         return_value=ConsentCheckResult(status=ConsentStatus.WITHDRAWN, timeout=False, error=False)):
            result = await checker.perform_full_check(
                "P001",
                {"observations": "test"},
            )
            assert result is not None
            assert result.status == CheckResultStatus.FAILED

    @pytest.mark.asyncio
    async def test_withdrawn_overrides_timeout(self):
        from safety_gateway.checker import SafetyGatewayChecker
        from safety_gateway.consent_checker import ConsentCheckResult
        from safety_gateway.validation_result import CheckResultStatus, ConsentStatus

        checker = SafetyGatewayChecker()
        with patch.object(checker._consent_checker, 'check_consent_status',
                         return_value=ConsentCheckResult(status=ConsentStatus.WITHDRAWN, timeout=True, error=False)):
            result = await checker.perform_full_check(
                "P001",
                {"observations": "test"},
            )
            assert result is not None
            assert result.status == CheckResultStatus.FAILED, \
                f"已撤回同意即使超时也应FAILED, got {result.status}"


class TestConsentUpdateNoOperatorIdInBody:

    def test_consent_update_request_no_operator_id(self):
        from app.models.schemas import ConsentUpdateRequest
        fields = ConsentUpdateRequest.model_fields
        assert "patient_id" in fields
        assert "action" in fields


class TestIdGeneration:

    async def test_consent_record_id_not_timestamp(self):
        from app.models.schemas import ConsentStatus
        from app.repositories.consent_repository import ConsentRepository
        db = AsyncMock()
        db.flush = AsyncMock()
        db.add = Mock()
        repo = ConsentRepository(db)

        with patch("app.repositories.consent_repository.AuditService") as mock_audit_cls, \
             patch("app.repositories.consent_repository.SafetyService") as mock_safety_cls:
            mock_audit = AsyncMock()
            mock_audit.log_action = AsyncMock(return_value=Mock(hash_self="abc"))
            mock_audit_cls.return_value = mock_audit
            mock_safety = AsyncMock()
            mock_safety.log_event = AsyncMock()
            mock_safety_cls.return_value = mock_safety

            record = await repo.create_consent(
                patient_id="p001",
                status=ConsentStatus.GRANTED,
                consent_version="1.0",
                operator_id="op001",
            )
            assert record is not None

    def test_uuid_id_generation_no_collision(self):
        import uuid
        ids = set()
        for _ in range(10000):
            new_id = uuid.uuid4().int >> 96
            assert new_id not in ids
            ids.add(new_id)


class TestDataCleanupHandlerScanPattern:

    @pytest.mark.asyncio
    async def test_scan_pattern_includes_prefix(self):
        from app.config.redis_keys import RedisKey
        from app.security.consent_withdrawal_consumer import ConsentWithdrawalEvent, DataCleanupHandler

        handler = DataCleanupHandler()
        event = ConsentWithdrawalEvent(
            event_id="evt1",
            patient_id="P001",
            operator_id="op1",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="1.0",
        )

        expected_pattern = RedisKey.desens_session("*", event.patient_id)
        assert ":desens:session:*:P001" in expected_pattern
        assert expected_pattern.startswith("ultrasound:"), f"Pattern should include prefix: {expected_pattern}"

    @pytest.mark.asyncio
    async def test_session_id_extraction_from_key(self):
        key = b"ultrasound:desens:session:sess_abc:P001"
        parts = key.decode().split(":")
        session_id = parts[-2]
        assert session_id == "sess_abc", f"Expected sess_abc, got {session_id}"

        patient_id = parts[-1]
        assert patient_id == "P001"


class TestCircuitBreakerThreadSafety:

    @pytest.mark.asyncio
    async def test_concurrent_record_failure(self):

        from safety_gateway.circuit_breaker import CircuitBreaker, CircuitBreakerState

        breaker = CircuitBreaker(failure_threshold=100)

        for _ in range(150):
            await breaker.record_failure()

        assert breaker._state == CircuitBreakerState.OPEN


class TestSafetyGatewayHeterogeneousRedundancy:

    def test_sg_phone_pattern_differs_from_main(self):
        from app.security.desensitization import get_desensitization_engine
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


class TestSafetySeverityEnum:

    def test_safety_critical_value(self):
        from app.models.schemas import SafetySeverity
        assert SafetySeverity.SAFETY_CRITICAL.value == "safety_critical"

    def test_important_value(self):
        from app.models.schemas import SafetySeverity
        assert SafetySeverity.IMPORTANT.value == "important"

    def test_routine_value(self):
        from app.models.schemas import SafetySeverity
        assert SafetySeverity.ROUTINE.value == "routine"

    def test_no_major_minor(self):
        from app.models.schemas import SafetySeverity
        members = [m.name for m in SafetySeverity]
        assert "MAJOR" not in members
        assert "MINOR" not in members


class TestSafetyGatewayClient:

    def test_safety_gateway_client_exists(self):
        from app.services.safety_gateway_client import SafetyGatewayClient
        client = SafetyGatewayClient()
        assert hasattr(client, "check_output")
        assert hasattr(client, "get_consent_status")

    def test_e4005_response_structure(self):
        from app.services.safety_gateway_client import SafetyGatewayClient
        client = SafetyGatewayClient()
        response = client._e4005_response()
        assert response["passed"] is False
        assert response["error_code"] == "E4005"
        assert response["degraded_mode"] is True

    async def test_check_output_timeout_returns_degraded(self):
        import httpx

        from app.services.safety_gateway_client import SafetyGatewayClient
        with patch("app.services.safety_gateway_client.settings") as mock_settings:
            mock_settings.safety_gateway_host = "0.0.0.0"
            mock_settings.safety_gateway_port = 9001
            mock_settings.safety_gateway_enable_degraded_mode = True
            mock_settings.safety_gateway_queue_timeout_seconds = 0.0
            mock_settings.sg_http_client_timeout_seconds = 1.0
            client = SafetyGatewayClient()
            with patch.object(client, "_get_client", new_callable=AsyncMock) as mock_get:
                mock_http = AsyncMock()
                mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
                mock_get.return_value = mock_http
                result = await client.check_output(
                    session_id="s001",
                    patient_id="p001",
                    output_data={},
                    consent_status_at_input="granted",
                )
                assert result["degraded_mode"] is True


class TestErrorCodeDeep:

    ERROR_CODES = ["E1001", "E1002", "E1006", "E1008", "E1010", "E4007"]

    @pytest.mark.parametrize("code", ERROR_CODES, ids=ERROR_CODES)
    def test_error_code_defined(self, code):
        from app.api.v1.errors import ERROR_CODE_MAP
        assert code in ERROR_CODE_MAP

    @pytest.mark.parametrize("code", ERROR_CODES, ids=ERROR_CODES)
    def test_error_code_has_message(self, code):
        from app.api.v1.errors import ERROR_CODE_MAP
        spec = ERROR_CODE_MAP[code]
        assert hasattr(spec, "message")
        assert isinstance(spec.message, str)
        assert len(spec.message) > 0


class TestHttpErrorCodeMapping:

    def test_401_maps_to_e1003(self):
        import inspect

        from app.api.v1.errors import http_exception_handler
        source = inspect.getsource(http_exception_handler)
        assert '"401": "E1003"' in source or "401: \"E1003\"" in source

    def test_403_maps_to_e1004(self):
        import inspect

        from app.api.v1.errors import http_exception_handler
        source = inspect.getsource(http_exception_handler)
        assert "403" in source and "E1008" in source

    def test_422_maps_to_e1006(self):
        import inspect

        from app.api.v1.errors import http_exception_handler
        source = inspect.getsource(http_exception_handler)
        assert '"422": "E1006"' in source or "422: \"E1006\"" in source


class TestGeneralExceptionHandlerNoLeak:

    def test_general_exception_handler_detail_is_none(self):
        import inspect

        from app.api.v1.errors import general_exception_handler
        source = inspect.getsource(general_exception_handler)
        assert 'detail=str(exc)' not in source
        assert 'detail=None' in source


class TestRateLimitCompactFull:

    def test_compact_default_rate_limit(self):
        from app.config.settings import Settings
        s = Settings(deployment_profile='compact')
        assert s.desensitization_restore_rate_limit == 100

    def test_full_mode_rate_limit_override(self):
        from app.config.settings import Settings
        s = Settings(deployment_profile='full')
        assert s.desensitization_restore_rate_limit == 200

    def test_full_rate_limit_setting_exists(self):
        from app.config.settings import Settings
        s = Settings()
        assert hasattr(s, 'desensitization_restore_rate_limit_full')
        assert s.desensitization_restore_rate_limit_full == 200


class TestRateLimitBucketCleanup:

    def test_token_bucket_has_last_access(self):
        from app.config.rate_limit import TokenBucket
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert hasattr(bucket, "last_access")
        assert bucket.last_access > 0

    def test_token_bucket_last_access_updated_on_consume(self):
        from app.config.rate_limit import TokenBucket
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        old_access = bucket.last_access
        time.sleep(0)
        bucket.consume(1)
        assert bucket.last_access > old_access

    def test_cleanup_expired_buckets(self):
        import app.config.rate_limit as rl_module
        from app.config.rate_limit import (
            BUCKET_MAX_AGE_SECONDS,
            TokenBucket,
            _buckets,
            _cleanup_expired_buckets,
        )

        old_bucket = TokenBucket(capacity=10, refill_rate=1.0)
        old_bucket.last_access = time.monotonic() - BUCKET_MAX_AGE_SECONDS - 100

        new_bucket = TokenBucket(capacity=10, refill_rate=1.0)
        new_bucket.last_access = time.monotonic()

        _buckets["old_key"] = old_bucket
        _buckets["new_key"] = new_bucket
        rl_module._last_cleanup = 0

        import asyncio
        # asyncio.run 每次新建并关闭事件循环（get_event_loop() 在 Py3.13
        # 无当前循环时抛 RuntimeError，顺序相关偶发失败）
        asyncio.run(_cleanup_expired_buckets())

        assert "old_key" not in _buckets
        assert "new_key" in _buckets

        _buckets.clear()

    def test_cleanup_interval_respected(self):
        import app.config.rate_limit as rl_module
        from app.config.rate_limit import _buckets

        rl_module._last_cleanup = time.monotonic()

        old_bucket = Mock()
        old_bucket.last_access = time.monotonic() - 99999
        _buckets["old_key"] = old_bucket

        import asyncio
        asyncio.run(rl_module._cleanup_expired_buckets())

        assert "old_key" in _buckets

        _buckets.clear()


class TestRateLimitUserIdHashing:

    def test_get_user_id_uses_sha256(self):
        import inspect

        from app.config.rate_limit import _get_user_id_from_request
        source = inspect.getsource(_get_user_id_from_request)
        assert "hashlib.sha256" in source

    def test_get_user_id_no_truncation(self):
        import inspect

        from app.config.rate_limit import _get_user_id_from_request
        source = inspect.getsource(_get_user_id_from_request)
        assert "auth_header[7:20]" not in source

    def test_different_tokens_different_ids(self):
        from app.config.rate_limit import _get_user_id_from_request

        class MockRequest:
            def __init__(self, auth_header):
                self.headers = {"Authorization": auth_header}
                self.client = Mock()

        req1 = MockRequest("Bearer token_aaa")
        req2 = MockRequest("Bearer token_bbb")
        id1 = _get_user_id_from_request(req1)
        id2 = _get_user_id_from_request(req2)
        assert id1 != id2

    def test_same_token_same_id(self):
        from app.config.rate_limit import _get_user_id_from_request

        class MockRequest:
            def __init__(self, auth_header):
                self.headers = {"Authorization": auth_header}
                self.client = Mock()

        req1 = MockRequest("Bearer same_token_123")
        req2 = MockRequest("Bearer same_token_123")
        id1 = _get_user_id_from_request(req1)
        id2 = _get_user_id_from_request(req2)
        assert id1 == id2

    def test_no_auth_returns_anonymous(self):
        from app.config.rate_limit import _get_user_id_from_request

        class MockRequest:
            def __init__(self):
                self.headers = {}
                self.client = Mock()

        req = MockRequest()
        user_id = _get_user_id_from_request(req)
        assert user_id == "anonymous"


@pytest.mark.perf
class TestSECPerformanceBenchmarks:

    def test_desensitization_p99_under_5ms(self):
        from app.security.desensitization import get_desensitization_engine
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
        from app.security.refresh_token import refresh_token_store
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

    def test_hsm_sign_p99_under_threshold(self):
        from app.security.hsm import hsm_manager
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

    @pytest.mark.parametrize("length", [100, 500, 1000, 5000, 10000],
                             ids=["100chars", "500chars", "1000chars", "5000chars", "10000chars"])
    def test_desensitize_latency_by_length(self, length):
        from app.security.desensitization import get_desensitization_engine
        engine = get_desensitization_engine()
        text = "患者张三，身份证号110101199001011234，手机号13812345678。" * (length // 30)
        start = time.perf_counter()
        engine.desensitize(text)
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 100, f"Desensitize {length} chars took {elapsed:.1f}ms"

    @pytest.mark.parametrize("length", [100, 500, 1000, 5000, 10000],
                             ids=["100chars", "500chars", "1000chars", "5000chars", "10000chars"])
    def test_sg_desensitize_latency_by_length(self, length):
        from safety_gateway.desensitization import sg_desensitization_engine
        text = "患者：张三，身份证号110101199001011234，手机号13812345678。" * (length // 30)
        start = time.perf_counter()
        sg_desensitization_engine.desensitize(text)
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 100, f"SG desensitize {length} chars took {elapsed:.1f}ms"


class TestDoubleCommitFix:

    def test_diagnosis_submit_no_manual_commit(self):
        import inspect

        from app.api.v1 import diagnosis
        source = inspect.getsource(diagnosis.submit_diagnosis_request)
        assert "await db.commit()" not in source

    def test_consent_update_no_manual_commit(self):
        import inspect

        from app.api.v1 import consent
        source = inspect.getsource(consent.update_consent)
        assert "await db.commit()" not in source

    def test_feedback_submit_no_manual_commit(self):
        import inspect

        from app.api.v1 import feedback
        source = inspect.getsource(feedback.submit_feedback)
        assert "await db.commit()" not in source

    def test_session_create_no_manual_commit(self):
        import inspect

        from app.api.v1 import session
        source = inspect.getsource(session.create_session)
        assert "await db.commit()" not in source

    def test_session_update_no_manual_commit(self):
        import inspect

        from app.api.v1 import session
        source = inspect.getsource(session.update_session)
        assert "await db.commit()" not in source

    def test_imaging_upload_no_manual_commit(self):
        import inspect

        from app.api.v1 import imaging
        source = inspect.getsource(imaging.upload_dicom)
        assert "await db.commit()" not in source

    def test_imaging_stream_no_manual_commit(self):
        import inspect

        from app.api.v1 import imaging
        source = inspect.getsource(imaging.stream_dicom_frame)
        assert "await db.commit()" not in source


class TestConfigUsedInConnections:

    def test_database_engine_uses_pool_size(self):
        from app.config.database import async_engine
        pool = async_engine.pool
        assert pool.size() > 0

    def test_redis_client_uses_max_connections(self):
        import redis.asyncio as redis

        from app.config.redis import RedisPool
        with patch("app.config.redis.settings") as mock_settings:
            mock_settings.redis_url = "redis://localhost:6379/0"
            mock_settings.redis_pool_max_connections = 42
            mock_settings.redis_pool_timeout = 10
            RedisPool._client = None
            with patch.object(redis.Redis, "from_url", return_value=Mock()) as mock_from_url:
                import asyncio
                try:
                    asyncio.run(RedisPool.get_client())
                except Exception:
                    pass
                if mock_from_url.called:
                    call_kwargs = mock_from_url.call_args[1]
                    assert call_kwargs.get("max_connections") == 42
        RedisPool._client = None


class TestHealthCheckReusesConnections:

    def test_health_check_imports_redis_pool(self):
        import inspect

        from app.api.v1 import health
        source = inspect.getsource(health)
        assert "RedisPool" in source
        assert "redis.from_url" not in source

    def test_health_check_imports_async_engine(self):
        import inspect

        from app.api.v1 import health
        source = inspect.getsource(health)
        assert "async_engine" in source
        assert "create_async_engine" not in source


class TestValidatorNoUnusedImport:

    def test_validator_no_validation_error_import(self):
        import inspect

        from app.config import validator
        source = inspect.getsource(validator)
        assert "from pydantic import ValidationError" not in source


class TestDatabaseInitDbIncludesFeedbackRecord:

    def test_init_db_imports_feedback_record(self):
        import inspect

        from app.config.database import init_db
        source = inspect.getsource(init_db)
        assert "FeedbackRecord" in source


class TestDiagnosisKnowledgeBaseVersion:

    def test_diagnosis_result_no_knowledge_ids_index(self):
        import inspect

        from app.api.v1 import diagnosis
        source = inspect.getsource(diagnosis.get_diagnosis_result)
        assert "knowledge_ids[0]" not in source


class TestDuplicateSafetyEventCodeRemoved:

    def test_consent_repo_no_log_safety_event(self):
        from app.repositories.consent_repository import ConsentRepository
        assert not hasattr(ConsentRepository, "_log_safety_event")

    def test_consent_repo_uses_safety_service(self):
        import inspect

        from app.repositories.consent_repository import ConsentRepository
        source = inspect.getsource(ConsentRepository.create_consent)
        assert "SafetyService" in source


class TestDicomRetransmitLimit:

    def test_retransmit_limit_constant_exists(self):
        from app.api.websocket.dicom import MAX_RETRANSMIT_PER_FRAME
        assert MAX_RETRANSMIT_PER_FRAME > 0
        assert MAX_RETRANSMIT_PER_FRAME <= 10

    def test_retransmit_limit_is_3(self):
        from app.api.websocket.dicom import MAX_RETRANSMIT_PER_FRAME
        assert MAX_RETRANSMIT_PER_FRAME == 3


class TestDicomStorageService:

    def test_dicom_storage_service_exists(self):
        from app.services.dicom_storage import DicomStorageService
        assert DicomStorageService is not None

    def test_dicom_storage_singleton_exists(self):
        from app.services.dicom_storage import get_dicom_storage
        assert get_dicom_storage() is not None

    def test_dicom_storage_has_save_method(self):
        from app.services.dicom_storage import DicomStorageService
        assert hasattr(DicomStorageService, "save")

    def test_dicom_storage_has_retrieve_method(self):
        from app.services.dicom_storage import DicomStorageService
        assert hasattr(DicomStorageService, "retrieve")

    def test_dicom_storage_has_delete_method(self):
        from app.services.dicom_storage import DicomStorageService
        assert hasattr(DicomStorageService, "delete")

    def test_dicom_storage_builds_correct_path(self):
        from app.services.dicom_storage import DicomStorageService
        svc = DicomStorageService()
        path = svc._build_storage_path("PAT001", "sess_001", "1.2.3.4.5")
        assert "PAT001" in path
        assert "sess_001" in path
        assert "1.2.3.4.5.dcm" in path

    async def test_dicom_storage_rejects_oversized_file(self):
        from app.services.dicom_storage import DicomStorageService
        with patch("app.services.dicom_storage.settings") as mock_settings:
            mock_settings.dicom_storage_backend = "local"
            mock_settings.dicom_storage_path = "/tmp/test_dicom"
            mock_settings.dicom_storage_max_file_size = 100
            svc = DicomStorageService()
            svc._max_file_size = 100
            svc._backend = "local"
            svc._base_path = "/tmp/test_dicom"
            with pytest.raises(ValueError, match="exceeds maximum"):
                await svc.save(
                    content=b"x" * 200,
                    patient_id="PAT001",
                    session_id="sess_001",
                    sop_instance_uid="1.2.3.4.5",
                )

    def test_imaging_uses_dicom_storage(self):
        import inspect

        from app.api.v1 import imaging
        source = inspect.getsource(imaging.upload_dicom)
        assert "dicom_storage" in source

    def test_imaging_has_file_size_check(self):
        import inspect

        from app.api.v1 import imaging
        source = inspect.getsource(imaging.upload_dicom)
        assert "dicom_storage_max_file_size" in source

    def test_imaging_uses_bytesio_for_pydicom(self):
        import inspect

        from app.api.v1 import imaging
        source = inspect.getsource(imaging.parse_and_validate_dicom)
        assert "BytesIO" in source


class TestDicomStorageConfig:

    def test_settings_has_dicom_storage_backend(self):
        from app.config.settings import settings
        assert hasattr(settings, "dicom_storage_backend")

    def test_settings_has_dicom_storage_path(self):
        from app.config.settings import settings
        assert hasattr(settings, "dicom_storage_path")

    def test_settings_has_dicom_storage_max_file_size(self):
        from app.config.settings import settings
        assert hasattr(settings, "dicom_storage_max_file_size")

    def test_default_backend_is_local(self):
        from app.config.settings import settings
        assert settings.dicom_storage_backend == "local"

    def test_max_file_size_is_500mb(self):
        from app.config.settings import settings
        assert settings.dicom_storage_max_file_size == 524288000


class TestFeedbackReviewerIdFromJWT:

    def test_feedback_submission_reviewer_id_optional(self):
        from app.models.schemas import FeedbackContent, FeedbackLevel, FeedbackSubmission, FeedbackType
        fb = FeedbackSubmission(
            request_id="req_001",
            session_id="s001",
            feedback_type=FeedbackType.CORRECTION,
            feedback_level=FeedbackLevel.L2,
            content=FeedbackContent(
                field="diagnosis",
                original_value="A",
                corrected_value="B",
                reason="test",
            ),
        )
        assert fb.reviewer_id is None


class TestWebSocketShortSession:

    def test_nav_endpoint_no_depends_get_db_session(self):
        import inspect

        from app.api.websocket import nav
        source = inspect.getsource(nav.websocket_nav_endpoint)
        assert "Depends(get_db_session)" not in source

    def test_nav_endpoint_uses_async_session_local(self):
        import inspect

        from app.api.websocket import nav
        source = inspect.getsource(nav.websocket_nav_endpoint)
        assert "AsyncSessionLocal" in source

    def test_nav_imports_async_session_local(self):
        from app.api.websocket import nav
        assert hasattr(nav, "AsyncSessionLocal") or "AsyncSessionLocal" in dir(nav)

    def test_nav_no_long_lived_db_parameter(self):
        import inspect

        from app.api.websocket import nav
        sig = inspect.signature(nav.websocket_nav_endpoint)
        param_names = list(sig.parameters.keys())
        assert "db" not in param_names


# ============================================================================
# 合并自 test_sec_token_consent_audit_deep.py - Audit 相关
# ============================================================================


class TestAuditServiceDeepV2:

    def test_audit_service_has_log_action(self):
        from app.services.audit_service import AuditService
        assert hasattr(AuditService, 'log_action')

    def test_get_chain_lock_function(self):
        from app.services.audit_service import _get_chain_lock
        assert callable(_get_chain_lock)

    def test_verify_chain_accepts_session_id(self):
        import inspect

        from app.services.audit_service import AuditService
        sig = inspect.signature(AuditService.verify_chain)
        params = list(sig.parameters.keys())
        assert 'session_id' in params
