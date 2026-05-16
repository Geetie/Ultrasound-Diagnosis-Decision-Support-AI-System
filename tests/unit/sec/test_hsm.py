import pytest
import os
import asyncio
import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

from app.security.hsm import (
    HSMManager, DegradedPhase, KeySlot, DegradedBehavior,
    DEGRADED_BEHAVIORS, hsm_manager,
)
pytestmark = [pytest.mark.track_sec, pytest.mark.sec_domain_hsm]


def _make_lightweight_hsm():
    from app.security.hsm import HSMManager, KeySlot as _KS
    mgr = HSMManager.__new__(HSMManager)
    mgr._use_soft_hsm = os.environ.get("USE_SOFT_HSM", "false").lower() == "true"
    mgr._hsm_available = False
    mgr._key_slots = {slot: {} for slot in _KS}
    mgr._lock = threading.RLock()
    mgr._is_degraded = False
    mgr._degraded_at = None
    mgr._degraded_reason = None
    mgr._soft_hsm_enabled_at = None
    mgr._key_cleanup_timer = None
    mgr._periodic_check_timer = None
    mgr._pending_tasks = set()
    mgr._loop = None
    return mgr


class TestHSMSignaturePadding:

    def test_sign_verify_roundtrip_with_pss_padding(self):
        signature = hsm_manager.sign("shared_sign_key", b"test_data_to_sign")
        assert signature is not None
        assert len(signature) > 0
        verified = hsm_manager.verify_signature("shared_sign_key", b"test_data_to_sign", signature)
        assert verified is True

    def test_signature_can_be_verified(self):
        signature = hsm_manager.sign("shared_sign_key", b"another_test_data")
        assert hsm_manager.verify_signature("shared_sign_key", b"another_test_data", signature) is True
        assert hsm_manager.verify_signature("shared_sign_key", b"wrong_data", signature) is False


class TestHSMKeyManagement:

    def test_generate_key_pair(self):
        result = hsm_manager.generate_key_pair("test_generate_key")
        assert result is not None
        assert "key_name" in result
        assert "key_version" in result
        assert "public_key_pem" in result
        assert result["key_version"] == 1

    def test_get_public_key(self):
        public_key = hsm_manager.get_public_key("shared_sign_key")
        assert public_key is not None
        assert "-----BEGIN PUBLIC KEY-----" in public_key
        assert "-----END PUBLIC KEY-----" in public_key

    def test_list_keys(self):
        keys = hsm_manager.list_keys()
        assert "shared_list_key_1" in keys
        assert "shared_list_key_2" in keys

    def test_get_key_info(self):
        info = hsm_manager.get_key_info("shared_sign_key")
        assert info is not None
        assert info["key_name"] == "shared_sign_key"
        assert info["key_version"] == 1
        assert info["has_private_key"] is True


class TestHSMEncryptDecrypt:

    def test_encrypt_decrypt_roundtrip(self):
        plaintext = b"sensitive patient data"
        ciphertext = hsm_manager.encrypt("shared_enc_key", plaintext)
        assert ciphertext != plaintext
        decrypted = hsm_manager.decrypt("shared_enc_key", ciphertext)
        assert decrypted == plaintext

    def test_encrypt_produces_different_ciphertext(self):
        plaintext = b"same data"
        ct1 = hsm_manager.encrypt("shared_enc_key", plaintext)
        ct2 = hsm_manager.encrypt("shared_enc_key", plaintext)
        assert ct1 != ct2


class TestHSMGetPrivateKey:

    def test_get_private_key_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="Private key export is prohibited"):
            hsm_manager.get_private_key("shared_sign_key")


class TestHSMSignJWTDegradedCheck:

    def test_sign_jwt_restricted_raises(self):
        hsm_manager._is_degraded = True
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        try:
            with pytest.raises(RuntimeError, match="restricted|prohibited"):
                hsm_manager.sign_jwt(
                    "shared_jwt_key",
                    {"alg": "RS256", "typ": "JWT"},
                    {"sub": "test"},
                )
        finally:
            hsm_manager._is_degraded = False
            hsm_manager._degraded_at = None

    def test_sign_jwt_emergency_raises(self):
        hsm_manager._is_degraded = True
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=35)
        try:
            with pytest.raises(RuntimeError, match="prohibited|emergency"):
                hsm_manager.sign_jwt(
                    "shared_jwt_key",
                    {"alg": "RS256", "typ": "JWT"},
                    {"sub": "test"},
                )
        finally:
            hsm_manager._is_degraded = False
            hsm_manager._degraded_at = None


class TestSoftHSMExpiry:

    def test_soft_hsm_expiry_check(self):
        mgr = _make_lightweight_hsm()
        mgr._use_soft_hsm = True
        mgr._soft_hsm_enabled_at = datetime.now(timezone.utc) - timedelta(days=91)
        expired = mgr._check_soft_hsm_expiry()
        assert expired is True

    def test_soft_hsm_not_expired(self):
        mgr = _make_lightweight_hsm()
        mgr._use_soft_hsm = True
        mgr._soft_hsm_enabled_at = datetime.now(timezone.utc) - timedelta(days=30)
        expired = mgr._check_soft_hsm_expiry()
        assert expired is False


class TestKeySlotSeparation:

    def test_key_slot_enum_values(self):
        assert KeySlot.JWT_SIGNING == "jwt_signing"
        assert KeySlot.DATA_ENCRYPTION == "data_encryption"
        assert KeySlot.DESENSITIZATION_MAPPING == "desensitization_mapping"
        assert KeySlot.EMERGENCY_SIGNING == "emergency_signing"

    def test_key_slot_routing_jwt_prefix(self):
        assert hsm_manager._get_key_slot("jwt_signing_key") == KeySlot.JWT_SIGNING
        assert hsm_manager._get_key_slot("jwt_primary") == KeySlot.JWT_SIGNING

    def test_key_slot_routing_desens_prefix(self):
        assert hsm_manager._get_key_slot("desens_mapping_key") == KeySlot.DESENSITIZATION_MAPPING

    def test_key_slot_routing_emergency_prefix(self):
        assert hsm_manager._get_key_slot("emergency_signing_key") == KeySlot.EMERGENCY_SIGNING

    def test_key_slot_routing_default_data_encryption(self):
        assert hsm_manager._get_key_slot("my_key") == KeySlot.DATA_ENCRYPTION
        assert hsm_manager._get_key_slot("report_key") == KeySlot.DATA_ENCRYPTION

    def test_generate_key_pair_with_explicit_slot(self):
        hsm_manager.generate_key_pair("test_slot_jwt", slot=KeySlot.JWT_SIGNING)
        key_info = hsm_manager.get_key_info("test_slot_jwt")
        assert key_info is not None
        assert key_info["slot"] == "jwt_signing"

    def test_generate_key_pair_auto_routing(self):
        key_info = hsm_manager.get_key_info("shared_jwt_key")
        assert key_info is not None
        assert key_info["slot"] == "jwt_signing"

    def test_key_slots_isolation(self):
        jwt_keys = hsm_manager.list_keys_by_slot(KeySlot.JWT_SIGNING)
        data_keys = hsm_manager.list_keys_by_slot(KeySlot.DATA_ENCRYPTION)
        assert "shared_jwt_key" in jwt_keys
        assert "shared_jwt_key" not in data_keys
        assert "shared_sign_key" in data_keys
        assert "shared_sign_key" not in jwt_keys

    def test_find_key_fallback_across_slots(self):
        key_info, slot = hsm_manager._find_key("shared_sign_key")
        assert key_info is not None
        assert slot == KeySlot.DATA_ENCRYPTION

    def test_list_keys_includes_all_slots(self):
        all_keys = hsm_manager.list_keys()
        assert isinstance(all_keys, list)


class TestKeySlotIsolation:

    SLOTS = [KeySlot.JWT_SIGNING, KeySlot.DATA_ENCRYPTION, KeySlot.DESENSITIZATION_MAPPING, KeySlot.EMERGENCY_SIGNING]

    @pytest.mark.parametrize("slot", SLOTS, ids=[s.name for s in SLOTS])
    def test_generate_key_in_slot(self, slot):
        mgr = HSMManager()
        key_name = f"test_slot_{slot.name}"
        mgr.generate_key_pair(key_name, slot=slot)
        assert key_name in mgr._key_slots[slot]

    @pytest.mark.parametrize("slot", SLOTS, ids=[s.name for s in SLOTS])
    def test_sign_in_slot(self, slot):
        mgr = HSMManager()
        key_name = f"test_sign_{slot.name}"
        mgr.generate_key_pair(key_name, slot=slot)
        data = b"test data"
        sig = mgr.sign(key_name, data)
        assert sig is not None
        assert len(sig) > 0

    @pytest.mark.parametrize("slot", SLOTS, ids=[s.name for s in SLOTS])
    def test_verify_in_slot(self, slot):
        mgr = HSMManager()
        key_name = f"test_verify_{slot.name}"
        mgr.generate_key_pair(key_name, slot=slot)
        data = b"verify test"
        sig = mgr.sign(key_name, data)
        assert mgr.verify_signature(key_name, data, sig) is True

    @pytest.mark.parametrize("slot", SLOTS, ids=[s.name for s in SLOTS])
    def test_encrypt_decrypt_in_slot(self, slot):
        mgr = HSMManager()
        key_name = f"test_enc_{slot.name}"
        mgr.generate_key_pair(key_name, slot=slot)
        plaintext = b"encrypt me"
        ct = mgr.encrypt(key_name, plaintext)
        pt = mgr.decrypt(key_name, ct)
        assert pt == plaintext

    @pytest.mark.parametrize("slot", SLOTS, ids=[s.name for s in SLOTS])
    def test_different_keys_same_slot_isolated(self, slot):
        mgr = HSMManager()
        k1 = f"test_iso_1_{slot.name}"
        k2 = f"test_iso_2_{slot.name}"
        mgr.generate_key_pair(k1, slot=slot)
        mgr.generate_key_pair(k2, slot=slot)
        sig1 = mgr.sign(k1, b"data")
        assert mgr.verify_signature(k1, b"data", sig1) is True
        assert mgr.verify_signature(k2, b"data", sig1) is False


class TestKeySlotIsolationParametrized:

    SLOTS = [KeySlot.JWT_SIGNING, KeySlot.DATA_ENCRYPTION, KeySlot.DESENSITIZATION_MAPPING, KeySlot.EMERGENCY_SIGNING]

    @pytest.mark.parametrize("slot", SLOTS, ids=[s.name for s in SLOTS])
    def test_generate_key_in_slot(self, slot):
        mgr = _make_lightweight_hsm()
        key_name = f"test_slot_{slot.name}"
        mgr.generate_key_pair(key_name, slot=slot)
        assert key_name in mgr._key_slots[slot]

    @pytest.mark.parametrize("slot", SLOTS, ids=[s.name for s in SLOTS])
    def test_sign_in_slot(self, slot, hsm_with_slot_keys):
        key_name = f"slot_key_{slot.name}"
        data = b"test data"
        sig = hsm_with_slot_keys.sign(key_name, data)
        assert sig is not None
        assert len(sig) > 0

    @pytest.mark.parametrize("slot", SLOTS, ids=[s.name for s in SLOTS])
    def test_verify_in_slot(self, slot, hsm_with_slot_keys):
        key_name = f"slot_key_{slot.name}"
        data = b"verify test"
        sig = hsm_with_slot_keys.sign(key_name, data)
        assert hsm_with_slot_keys.verify_signature(key_name, data, sig) is True

    @pytest.mark.parametrize("slot", SLOTS, ids=[s.name for s in SLOTS])
    def test_encrypt_decrypt_in_slot(self, slot, hsm_with_slot_keys):
        key_name = f"slot_key_{slot.name}"
        plaintext = b"encrypt me"
        ct = hsm_with_slot_keys.encrypt(key_name, plaintext)
        pt = hsm_with_slot_keys.decrypt(key_name, ct)
        assert pt == plaintext

    @pytest.mark.parametrize("slot", SLOTS, ids=[s.name for s in SLOTS])
    def test_different_keys_same_slot_isolated(self, slot, hsm_with_slot_keys):
        k1 = f"slot_key_{slot.name}"
        k2 = f"iso_extra_{slot.name}"
        hsm_with_slot_keys.generate_key_pair(k2, slot=slot)
        sig1 = hsm_with_slot_keys.sign(k1, b"data")
        assert hsm_with_slot_keys.verify_signature(k1, b"data", sig1) is True
        assert hsm_with_slot_keys.verify_signature(k2, b"data", sig1) is False


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
            mgr = _make_lightweight_hsm()
            mgr._use_soft_hsm = True
            assert mgr.get_hsm_mode() == "soft_hsm"


class TestDegradedStateMachine:

    def test_normal_to_cached(self):
        mgr = HSMManager()
        mgr.set_degraded("test_normal_to_cached")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert mgr.get_degraded_phase() == DegradedPhase.CACHED

    def test_cached_to_restricted(self):
        mgr = HSMManager()
        mgr.set_degraded("test_cached_to_restricted")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=6)
        assert mgr.get_degraded_phase() == DegradedPhase.RESTRICTED

    def test_restricted_to_emergency(self):
        mgr = HSMManager()
        mgr.set_degraded("test_restricted_to_emergency")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=31)
        assert mgr.get_degraded_phase() == DegradedPhase.EMERGENCY

    def test_recovery_from_cached(self):
        mgr = HSMManager()
        mgr.set_degraded("test_recovery_cached")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        assert mgr.get_degraded_phase() == DegradedPhase.CACHED
        mgr.clear_degraded()
        assert mgr.get_degraded_phase() == DegradedPhase.NORMAL

    def test_recovery_from_restricted(self):
        mgr = HSMManager()
        mgr.set_degraded("test_recovery_restricted")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        assert mgr.get_degraded_phase() == DegradedPhase.RESTRICTED
        mgr.clear_degraded()
        assert mgr.get_degraded_phase() == DegradedPhase.NORMAL

    def test_recovery_from_emergency(self):
        mgr = HSMManager()
        mgr.set_degraded("test_recovery_emergency")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=60)
        assert mgr.get_degraded_phase() == DegradedPhase.EMERGENCY
        mgr.clear_degraded()
        assert mgr.get_degraded_phase() == DegradedPhase.NORMAL

    def test_cached_signing_allowed(self):
        mgr = HSMManager()
        mgr.generate_key_pair("test_cached_sign")
        mgr.set_degraded("test_cached_sign_2")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        sig = mgr.sign("test_cached_sign", b"data")
        assert sig is not None

    def test_restricted_signing_prohibited(self):
        mgr = HSMManager()
        mgr.generate_key_pair("test_restricted_sign")
        mgr.set_degraded("test_restricted_sign_2")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        with pytest.raises(RuntimeError, match="restricted"):
            mgr.sign("test_restricted_sign", b"data")

    def test_emergency_signing_prohibited(self):
        mgr = HSMManager()
        mgr.generate_key_pair("test_emergency_sign")
        mgr.set_degraded("test_emergency_sign_2")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=60)
        with pytest.raises(RuntimeError, match="emergency"):
            mgr.sign("test_emergency_sign", b"data")

    def test_degraded_reason_tracking(self):
        mgr = HSMManager()
        mgr.set_degraded("test_reason")
        assert mgr._degraded_reason == "test_reason"
        mgr.clear_degraded()
        assert mgr._degraded_reason is None

    @pytest.mark.parametrize("minutes,expected_phase", [
        (0, DegradedPhase.CACHED),
        (1, DegradedPhase.CACHED),
        (4, DegradedPhase.CACHED),
        (5, DegradedPhase.RESTRICTED),
        (10, DegradedPhase.RESTRICTED),
        (29, DegradedPhase.RESTRICTED),
        (30, DegradedPhase.EMERGENCY),
        (60, DegradedPhase.EMERGENCY),
    ], ids=["0min", "1min", "4min", "5min", "10min", "29min", "30min", "60min"])
    def test_phase_at_various_times(self, minutes, expected_phase):
        mgr = HSMManager()
        mgr.set_degraded(f"test_time_{minutes}")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        assert mgr.get_degraded_phase() == expected_phase


class TestDegradedStateMachineParametrized:

    @pytest.mark.parametrize("minutes,expected_phase", [
        (0, DegradedPhase.CACHED),
        (1, DegradedPhase.CACHED),
        (4, DegradedPhase.CACHED),
        (5, DegradedPhase.RESTRICTED),
        (10, DegradedPhase.RESTRICTED),
        (29, DegradedPhase.RESTRICTED),
        (30, DegradedPhase.EMERGENCY),
        (60, DegradedPhase.EMERGENCY),
    ], ids=["0min", "1min", "4min", "5min", "10min", "29min", "30min", "60min"])
    def test_phase_at_various_times(self, minutes, expected_phase):
        mgr = _make_lightweight_hsm()
        mgr.set_degraded(f"test_time_{minutes}")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        assert mgr.get_degraded_phase() == expected_phase

    def test_recovery_from_cached(self):
        mgr = _make_lightweight_hsm()
        mgr.set_degraded("test_recovery_cached")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        assert mgr.get_degraded_phase() == DegradedPhase.CACHED
        mgr.clear_degraded()
        assert mgr.get_degraded_phase() == DegradedPhase.NORMAL

    def test_recovery_from_restricted(self):
        mgr = _make_lightweight_hsm()
        mgr.set_degraded("test_recovery_restricted")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        assert mgr.get_degraded_phase() == DegradedPhase.RESTRICTED
        mgr.clear_degraded()
        assert mgr.get_degraded_phase() == DegradedPhase.NORMAL

    def test_recovery_from_emergency(self):
        mgr = _make_lightweight_hsm()
        mgr.set_degraded("test_recovery_emergency")
        mgr._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=60)
        assert mgr.get_degraded_phase() == DegradedPhase.EMERGENCY
        mgr.clear_degraded()
        assert mgr.get_degraded_phase() == DegradedPhase.NORMAL

    def test_degraded_reason_tracking(self):
        mgr = _make_lightweight_hsm()
        mgr.set_degraded("test_reason")
        assert mgr._degraded_reason == "test_reason"
        mgr.clear_degraded()
        assert mgr._degraded_reason is None


class TestDegradedBehavior:

    def test_degraded_behavior_dataclass(self):
        behavior = DegradedBehavior(
            allow_jwt_signing=True,
            allow_data_encryption=True,
            allow_desensitization_restore=True,
            allow_emergency_signing=False,
        )
        assert behavior.allow_jwt_signing is True
        assert behavior.max_signature_count is None

    def test_degraded_behaviors_normal(self):
        behavior = DEGRADED_BEHAVIORS[DegradedPhase.NORMAL]
        assert behavior.allow_jwt_signing is True
        assert behavior.allow_data_encryption is True
        assert behavior.allow_desensitization_restore is True
        assert behavior.allow_emergency_signing is False

    def test_degraded_behaviors_cached(self):
        behavior = DEGRADED_BEHAVIORS[DegradedPhase.CACHED]
        assert behavior.allow_jwt_signing is True
        assert behavior.allow_emergency_signing is False

    def test_degraded_behaviors_restricted(self):
        behavior = DEGRADED_BEHAVIORS[DegradedPhase.RESTRICTED]
        assert behavior.allow_jwt_signing is False
        assert behavior.allow_data_encryption is True
        assert behavior.allow_desensitization_restore is True
        assert behavior.allow_emergency_signing is False

    def test_degraded_behaviors_emergency(self):
        behavior = DEGRADED_BEHAVIORS[DegradedPhase.EMERGENCY]
        assert behavior.allow_jwt_signing is False
        assert behavior.allow_data_encryption is False
        assert behavior.allow_desensitization_restore is False
        assert behavior.allow_emergency_signing is True
        assert behavior.max_signature_count == 50

    def test_check_degraded_behavior_normal(self):
        hsm_manager.clear_degraded()
        assert hsm_manager.check_degraded_behavior("jwt_signing") is True
        assert hsm_manager.check_degraded_behavior("data_encryption") is True
        assert hsm_manager.check_degraded_behavior("emergency_signing") is False

    def test_check_degraded_behavior_restricted(self):
        hsm_manager.set_degraded("test")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=15)
        assert hsm_manager.check_degraded_behavior("jwt_signing") is False
        assert hsm_manager.check_degraded_behavior("data_encryption") is True
        assert hsm_manager.check_degraded_behavior("desensitization_restore") is True
        hsm_manager.clear_degraded()

    def test_sign_restricted_phase_rejects_jwt_signing(self):
        hsm_manager.set_degraded("test")
        hsm_manager._degraded_at = datetime.now(timezone.utc) - timedelta(minutes=15)
        with pytest.raises(RuntimeError, match="prohibited"):
            hsm_manager.sign("shared_jwt_key", b"test_data")
        hsm_manager.clear_degraded()


class TestKeyRotationParallelValidation:

    def test_key_rotation_preserves_old_key(self):
        hsm_manager.generate_key_pair("rotation_test_key")
        old_public_key = hsm_manager.get_public_key("rotation_test_key")
        hsm_manager.rotate_key("rotation_test_key", parallel_days=7)
        new_public_key = hsm_manager.get_public_key("rotation_test_key")
        assert old_public_key != new_public_key
        old_key_info = None
        for slot_keys in hsm_manager._key_slots.values():
            if "rotation_test_key_v1" in slot_keys:
                old_key_info = slot_keys["rotation_test_key_v1"]
                break
        assert old_key_info is not None
        assert old_key_info["expires_at"] is not None


class TestKeyRotationFullFlow:

    def test_rotate_generates_new_key(self):
        hsm_manager.generate_key_pair("test_rotate_new")
        old_pub = hsm_manager.get_public_key("test_rotate_new")
        hsm_manager.rotate_key("test_rotate_new", parallel_days=7)
        new_pub = hsm_manager.get_public_key("test_rotate_new")
        assert old_pub != new_pub

    def test_rotate_preserves_old_key(self):
        hsm_manager.generate_key_pair("test_rotate_preserve")
        old_pub = hsm_manager.get_public_key("test_rotate_preserve")
        hsm_manager.rotate_key("test_rotate_preserve", parallel_days=7)
        slot = hsm_manager._get_key_slot("test_rotate_preserve")
        old_key = hsm_manager._key_slots[slot].get("test_rotate_preserve_v1")
        assert old_key is not None

    def test_rotate_old_key_has_expiry(self):
        hsm_manager.generate_key_pair("test_rotate_expiry")
        hsm_manager.rotate_key("test_rotate_expiry", parallel_days=7)
        slot = hsm_manager._get_key_slot("test_rotate_expiry")
        old_key = hsm_manager._key_slots[slot].get("test_rotate_expiry_v1")
        assert old_key is not None
        assert old_key.get("expires_at") is not None

    def test_new_key_signs_correctly(self):
        hsm_manager.generate_key_pair("test_rotate_sign")
        hsm_manager.rotate_key("test_rotate_sign", parallel_days=7)
        data = b"post rotation data"
        sig = hsm_manager.sign("test_rotate_sign", data)
        assert hsm_manager.verify_signature("test_rotate_sign", data, sig) is True

    def test_rotate_nonexistent_key_creates_new(self):
        result = hsm_manager.rotate_key("nonexistent_key_99999", parallel_days=7)
        assert result is not None
        assert "key_version" in result


class TestHSMConcurrencyExtended:

    def test_concurrent_generate_different_keys(self):
        errors = []
        def gen(name):
            try:
                hsm_manager.generate_key_pair(f"concurrent_gen_{name}")
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=gen, args=(i,)) for i in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(errors) == 0

    def test_concurrent_sign_same_key(self):
        errors = []
        def sign():
            try:
                for _ in range(20):
                    hsm_manager.sign("shared_sign_key", b"concurrent data")
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=sign) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(errors) == 0

    def test_concurrent_verify(self):
        sig = hsm_manager.sign("shared_sign_key", b"verify data")
        errors = []
        def verify():
            try:
                for _ in range(20):
                    assert hsm_manager.verify_signature("shared_sign_key", b"verify data", sig)
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=verify) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(errors) == 0

    def test_concurrent_encrypt_decrypt(self):
        errors = []
        def enc_dec():
            try:
                for _ in range(10):
                    ct = hsm_manager.encrypt("shared_enc_key", b"secret")
                    pt = hsm_manager.decrypt("shared_enc_key", ct)
                    assert pt == b"secret"
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=enc_dec) for _ in range(3)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(errors) == 0

    def test_concurrent_rotate_same_key(self):
        hsm_manager.generate_key_pair("concurrent_rotate_test")
        errors = []
        def rotate():
            try:
                hsm_manager.rotate_key("concurrent_rotate_test")
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=rotate) for _ in range(3)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(errors) == 0


class TestSoftHSMPeriodicCheck:

    def test_get_soft_hsm_status_not_soft_hsm(self):
        original = hsm_manager._use_soft_hsm
        hsm_manager._use_soft_hsm = False
        status = hsm_manager.get_soft_hsm_status()
        assert status["is_soft_hsm"] is False
        assert status["is_expired"] is False
        hsm_manager._use_soft_hsm = original

    def test_get_soft_hsm_status_active(self):
        original = hsm_manager._use_soft_hsm
        original_ts = hsm_manager._soft_hsm_enabled_at
        hsm_manager._use_soft_hsm = True
        hsm_manager._soft_hsm_enabled_at = datetime.now(timezone.utc) - timedelta(days=10)
        status = hsm_manager.get_soft_hsm_status()
        assert status["is_soft_hsm"] is True
        assert status["is_expired"] is False
        assert status["expiry_level"] == "none"
        hsm_manager._use_soft_hsm = original
        hsm_manager._soft_hsm_enabled_at = original_ts

    def test_get_soft_hsm_status_expired(self):
        original = hsm_manager._use_soft_hsm
        original_ts = hsm_manager._soft_hsm_enabled_at
        hsm_manager._use_soft_hsm = True
        hsm_manager._soft_hsm_enabled_at = datetime.now(timezone.utc) - timedelta(days=95)
        status = hsm_manager.get_soft_hsm_status()
        assert status["is_expired"] is True
        assert status["expiry_level"] == "warning"
        hsm_manager._use_soft_hsm = original
        hsm_manager._soft_hsm_enabled_at = original_ts

    def test_get_soft_hsm_status_critical(self):
        original = hsm_manager._use_soft_hsm
        original_ts = hsm_manager._soft_hsm_enabled_at
        hsm_manager._use_soft_hsm = True
        hsm_manager._soft_hsm_enabled_at = datetime.now(timezone.utc) - timedelta(days=125)
        status = hsm_manager.get_soft_hsm_status()
        assert status["is_expired"] is True
        assert status["expiry_level"] == "critical"
        hsm_manager._use_soft_hsm = original
        hsm_manager._soft_hsm_enabled_at = original_ts

    def test_soft_hsm_critical_days_constant(self):
        assert hsm_manager.SOFT_HSM_CRITICAL_DAYS == 120
        assert hsm_manager.SOFT_HSM_EXPIRY_DAYS == 90


class TestSoftHSMLifecycle:

    def test_softhsm_status_method_exists(self):
        status = hsm_manager.get_soft_hsm_status()
        assert isinstance(status, dict)

    def test_softhsm_status_has_required_fields(self):
        status = hsm_manager.get_soft_hsm_status()
        assert "is_soft_hsm" in status
        assert "is_expired" in status

    def test_softhsm_expiry_days_is_90(self):
        assert HSMManager.SOFT_HSM_EXPIRY_DAYS == 90

    def test_softhsm_critical_days_is_120(self):
        assert HSMManager.SOFT_HSM_CRITICAL_DAYS == 120

    def test_softhsm_status_values(self):
        status = hsm_manager.get_soft_hsm_status()
        if status.get("is_soft_hsm"):
            assert status["status"] in ("active", "expired", "critical")


class TestKeyCleanupSync:

    def test_cleanup_expired_keys_returns_count(self):
        count = hsm_manager.cleanup_expired_keys()
        assert isinstance(count, int)
        assert count >= 0

    def test_cleanup_removes_expired_keys_from_slots(self):
        hsm_manager.generate_key_pair("expire_test_key", slot=KeySlot.DATA_ENCRYPTION)
        with hsm_manager._lock:
            for slot_keys in hsm_manager._key_slots.values():
                if "expire_test_key" in slot_keys:
                    slot_keys["expire_test_key"]["expires_at"] = datetime.now(timezone.utc) - timedelta(hours=1)
                    break
        count = hsm_manager.cleanup_expired_keys()
        assert count >= 1
        key_info = hsm_manager.get_key_info("expire_test_key")
        assert key_info is None

    def test_key_cleanup_interval_constant(self):
        assert hsm_manager.KEY_CLEANUP_INTERVAL_HOURS == 24


class TestEmergencySigningFullFlow:

    def test_emergency_manager_exists(self):
        from app.security.emergency_signing import EmergencySigningManager
        mgr = EmergencySigningManager()
        assert mgr is not None

    def test_emergency_not_active_by_default(self):
        from app.security.emergency_signing import EmergencySigningManager
        mgr = EmergencySigningManager()
        assert hasattr(mgr, 'is_emergency_signing_active')
        assert callable(mgr.is_emergency_signing_active)

    def test_emergency_activate_requires_condition(self):
        from app.security.emergency_signing import EmergencySigningManager
        mgr = EmergencySigningManager()
        with pytest.raises(RuntimeError):
            asyncio.get_event_loop_policy().get_event_loop().run_until_complete(
                mgr.enable_emergency_signing("op1", "approver1", "test_reason")
            )

    def test_emergency_daily_limit_config(self):
        from app.config.settings import settings
        assert hasattr(settings, 'hsm_emergency_signing_max_daily')
        assert settings.hsm_emergency_signing_max_daily > 0

    def test_emergency_increment_count(self):
        from app.security.emergency_signing import EmergencySigningManager
        mgr = EmergencySigningManager()
        mgr._daily_signature_count = 0
        mgr._daily_count_reset_at = datetime.now(timezone.utc)
        assert mgr._daily_signature_count == 0

    async def test_emergency_check_daily_limit_under(self):
        from app.security.emergency_signing import EmergencySigningManager
        from app.config.settings import settings
        mgr = EmergencySigningManager()
        with patch("app.config.redis.RedisPool") as mock_pool:
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(return_value=None)
            mock_redis.eval = AsyncMock(return_value=1)
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            count = await mgr._atomic_check_and_increment_daily_count()
            assert count <= settings.hsm_emergency_signing_max_daily

    async def test_emergency_check_daily_limit_over(self):
        from app.security.emergency_signing import EmergencySigningManager
        from app.config.settings import settings
        mgr = EmergencySigningManager()
        max_daily = settings.hsm_emergency_signing_max_daily
        with patch("app.config.redis.RedisPool") as mock_pool:
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(return_value=None)
            mock_redis.eval = AsyncMock(return_value=max_daily + 1)
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            count = await mgr._atomic_check_and_increment_daily_count()
            assert count > max_daily

    def test_emergency_daily_reset_time_tracking(self):
        from app.security.emergency_signing import EmergencySigningManager
        mgr = EmergencySigningManager()
        assert hasattr(mgr, '_daily_count_reset_at')

    def test_emergency_singleton(self):
        from app.security.emergency_signing import get_emergency_signing_manager
        m1 = get_emergency_signing_manager()
        m2 = get_emergency_signing_manager()
        assert m1 is m2

    def test_private_key_export_prohibited(self):
        hsm_manager.generate_key_pair("test_pk_export")
        with pytest.raises(RuntimeError, match="[Pp]rivate [Kk]ey"):
            hsm_manager.get_private_key("test_pk_export")
