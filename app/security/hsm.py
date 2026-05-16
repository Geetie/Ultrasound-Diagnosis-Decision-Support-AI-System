from typing import Optional, Dict, Any, List, Tuple
from enum import Enum
from dataclasses import dataclass, field
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet
import os
import structlog
import threading
import subprocess
import re
import json
import asyncio
from datetime import datetime, timezone, timedelta

logger = structlog.get_logger()


class DegradedPhase(str, Enum):
    NORMAL = "normal"
    CACHED = "cached"
    RESTRICTED = "restricted"
    EMERGENCY = "emergency"


class KeySlot(str, Enum):
    JWT_SIGNING = "jwt_signing"
    DATA_ENCRYPTION = "data_encryption"
    DESENSITIZATION_MAPPING = "desensitization_mapping"
    EMERGENCY_SIGNING = "emergency_signing"


@dataclass
class DegradedBehavior:
    allow_jwt_signing: bool
    allow_data_encryption: bool
    allow_desensitization_restore: bool
    allow_emergency_signing: bool
    max_signature_count: Optional[int] = None


DEGRADED_BEHAVIORS: Dict[DegradedPhase, DegradedBehavior] = {
    DegradedPhase.NORMAL: DegradedBehavior(
        allow_jwt_signing=True,
        allow_data_encryption=True,
        allow_desensitization_restore=True,
        allow_emergency_signing=False,
    ),
    DegradedPhase.CACHED: DegradedBehavior(
        allow_jwt_signing=True,
        allow_data_encryption=True,
        allow_desensitization_restore=True,
        allow_emergency_signing=False,
    ),
    DegradedPhase.RESTRICTED: DegradedBehavior(
        allow_jwt_signing=False,
        allow_data_encryption=True,
        allow_desensitization_restore=True,
        allow_emergency_signing=False,
    ),
    DegradedPhase.EMERGENCY: DegradedBehavior(
        allow_jwt_signing=False,
        allow_data_encryption=False,
        allow_desensitization_restore=False,
        allow_emergency_signing=True,
        max_signature_count=50,
    ),
}

_KEY_SLOT_ROUTING = {
    "jwt_": KeySlot.JWT_SIGNING,
    "desens_": KeySlot.DESENSITIZATION_MAPPING,
    "emergency_": KeySlot.EMERGENCY_SIGNING,
}


class HSMManager:
    @property
    def DEGRADED_PHASE_1_THRESHOLD(self):
        from app.config.settings import settings
        return timedelta(minutes=settings.hsm_degraded_phase_1_threshold_minutes)

    @property
    def DEGRADED_PHASE_2_THRESHOLD(self):
        from app.config.settings import settings
        return timedelta(minutes=settings.hsm_degraded_phase_2_threshold_minutes)
    SOFT_HSM_EXPIRY_DAYS = 90
    SOFT_HSM_CRITICAL_DAYS = 120
    KEY_CLEANUP_INTERVAL_HOURS = 24

    def __init__(self) -> None:
        self._use_soft_hsm: bool = os.environ.get("USE_SOFT_HSM", "false").lower() == "true"
        self._hsm_available: bool = self._check_hsm_availability()
        self._key_slots: Dict[KeySlot, Dict[str, Dict[str, Any]]] = {
            slot: {} for slot in KeySlot
        }
        self._lock: threading.RLock = threading.RLock()

        self._is_degraded: bool = False
        self._degraded_at: Optional[datetime] = None
        self._degraded_reason: Optional[str] = None

        self._soft_hsm_enabled_at: Optional[datetime] = None
        self._load_soft_hsm_timestamp()

        if self._use_soft_hsm:
            self._check_soft_hsm_expiry()

        self._pending_tasks: set = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending_persistence: list = []

        self._soft_hsm_check_timer: Optional[threading.Timer] = None
        self._key_cleanup_timer: Optional[threading.Timer] = None

        self._restore_degraded_state_from_redis()
        self._restore_keys_from_redis()
        self._migrate_legacy_keys()

        import sys
        is_test = 'pytest' in sys.modules or 'PYTEST_CURRENT_TEST' in os.environ
        if not is_test:
            self._get_encryption_key()

        if self._use_soft_hsm and not is_test:
            self._start_soft_hsm_periodic_check()
        if not is_test:
            self._start_key_cleanup_timer()

    def _get_key_slot(self, key_name: str) -> KeySlot:
        for prefix, slot in _KEY_SLOT_ROUTING.items():
            if key_name.startswith(prefix):
                return slot
        return KeySlot.DATA_ENCRYPTION

    def _find_key(self, key_name: str) -> Tuple[Optional[Dict[str, Any]], Optional[KeySlot]]:
        slot = self._get_key_slot(key_name)
        key_info = self._key_slots[slot].get(key_name)
        if key_info:
            return key_info, slot
        for s, keys in self._key_slots.items():
            if key_name in keys:
                return keys[key_name], s
        return None, None

    def _load_soft_hsm_timestamp(self) -> None:
        timestamp_path = os.environ.get("SOFT_HSM_TIMESTAMP_PATH", "soft_hsm_enabled_at.txt")
        if os.path.exists(timestamp_path):
            try:
                with open(timestamp_path, "r") as f:
                    timestamp_str = f.read().strip()
                    self._soft_hsm_enabled_at = datetime.fromisoformat(timestamp_str)
                    logger.info("soft_hsm_timestamp_loaded", enabled_at=self._soft_hsm_enabled_at)
            except Exception as e:
                logger.error("failed_to_load_soft_hsm_timestamp", error=str(e))
        else:
            if self._use_soft_hsm:
                self._soft_hsm_enabled_at = datetime.now(timezone.utc)
                self._save_soft_hsm_timestamp()

    def _save_soft_hsm_timestamp(self) -> None:
        timestamp_path = os.environ.get("SOFT_HSM_TIMESTAMP_PATH", "soft_hsm_enabled_at.txt")
        try:
            with open(timestamp_path, "w") as f:
                f.write(self._soft_hsm_enabled_at.isoformat())
            logger.info("soft_hsm_timestamp_saved", enabled_at=self._soft_hsm_enabled_at)
        except Exception as e:
            logger.error("failed_to_save_soft_hsm_timestamp", error=str(e))

    def _safe_run_async_task(self, coro):
        try:
            import sys
            is_test = 'pytest' in sys.modules or 'PYTEST_CURRENT_TEST' in os.environ

            try:
                loop = asyncio.get_event_loop_policy().get_event_loop()
                if loop.is_running():
                    if is_test:
                        task = asyncio.ensure_future(coro)
                    else:
                        task = loop.create_task(coro)
                    self._pending_tasks.add(task)
                    task.add_done_callback(self._on_task_done)
                    return task
                else:
                    loop.run_until_complete(coro)
                    return None
            except RuntimeError:
                if not is_test:
                    logger.error("no_event_loop_for_redis_persistence", action="queued_for_later")
                    self._pending_persistence.append(coro)
                else:
                    coro.close()
                return None
        except Exception as e:
            logger.error("failed_to_start_async_task", error=str(e))

    def _on_task_done(self, task):
        self._pending_tasks.discard(task)
        try:
            exception = task.exception()
            if exception:
                logger.error("async_task_failed", error=str(exception))
        except asyncio.CancelledError:
            logger.warning("async_task_cancelled")
        except Exception as e:
            logger.error("async_task_done_callback_error", error=str(e))

    async def cleanup_pending_tasks(self) -> None:
        if self._pending_tasks:
            await asyncio.gather(*list(self._pending_tasks), return_exceptions=True)
            self._pending_tasks.clear()

    def _flush_pending_persistence(self) -> None:
        if not self._pending_persistence:
            return
        pending = list(self._pending_persistence)
        self._pending_persistence.clear()
        for coro in pending:
            self._safe_run_async_task(coro)
        logger.info("hsm_pending_persistence_flushed", count=len(pending))

    def _check_soft_hsm_expiry(self) -> bool:
        if self._soft_hsm_enabled_at is None:
            return False

        expiry_threshold = timedelta(days=self.SOFT_HSM_EXPIRY_DAYS)
        now = datetime.now(timezone.utc)
        time_since_enabled = now - self._soft_hsm_enabled_at

        if time_since_enabled > expiry_threshold:
            days_overdue = (time_since_enabled - expiry_threshold).days
            logger.error(
                "soft_hsm_expired",
                days_overdue=days_overdue,
                enabled_at=self._soft_hsm_enabled_at.isoformat()
            )
            return True

        days_remaining = (expiry_threshold - time_since_enabled).days
        if days_remaining <= 7:
            logger.warning(
                "soft_hsm_approaching_expiry",
                days_remaining=days_remaining
            )

        return False

    def _start_soft_hsm_periodic_check(self) -> None:
        interval_hours = 24
        interval_seconds = interval_hours * 3600

        def _check_and_reschedule():
            if not self._use_soft_hsm:
                return
            status = self.get_soft_hsm_status()
            if status["is_expired"]:
                if status["days_running"] > self.SOFT_HSM_CRITICAL_DAYS:
                    logger.critical(
                        "soft_hsm_critical_overdue",
                        days_running=status["days_running"],
                        days_over_critical=status["days_running"] - self.SOFT_HSM_CRITICAL_DAYS,
                    )
                else:
                    logger.error(
                        "soft_hsm_periodic_expiry_warning",
                        days_running=status["days_running"],
                    )
            self._soft_hsm_check_timer = threading.Timer(interval_seconds, _check_and_reschedule)
            self._soft_hsm_check_timer.daemon = True
            self._soft_hsm_check_timer.start()

        self._soft_hsm_check_timer = threading.Timer(interval_seconds, _check_and_reschedule)
        self._soft_hsm_check_timer.daemon = True
        self._soft_hsm_check_timer.start()

    def get_soft_hsm_status(self) -> Dict[str, Any]:
        if not self._use_soft_hsm or self._soft_hsm_enabled_at is None:
            return {
                "is_soft_hsm": self._use_soft_hsm,
                "days_running": 0,
                "is_expired": False,
                "expiry_level": "none",
            }

        now = datetime.now(timezone.utc)
        time_since_enabled = now - self._soft_hsm_enabled_at
        is_expired = time_since_enabled > timedelta(days=self.SOFT_HSM_EXPIRY_DAYS)
        days_running = time_since_enabled.days

        if days_running > self.SOFT_HSM_CRITICAL_DAYS:
            expiry_level = "critical"
        elif is_expired:
            expiry_level = "warning"
        elif days_running > self.SOFT_HSM_EXPIRY_DAYS - 7:
            expiry_level = "approaching"
        else:
            expiry_level = "none"

        return {
            "is_soft_hsm": True,
            "days_running": days_running,
            "is_expired": is_expired,
            "expiry_level": expiry_level,
            "enabled_at": self._soft_hsm_enabled_at.isoformat(),
        }

    def _start_key_cleanup_timer(self) -> None:
        interval_seconds = self.KEY_CLEANUP_INTERVAL_HOURS * 3600

        def _cleanup_and_reschedule():
            count = self.cleanup_expired_keys()
            if count > 0:
                logger.info("hsm_scheduled_key_cleanup_completed", expired_count=count)
            self._check_jwt_key_rotation()
            self._check_desensitization_key_rotation()
            self._key_cleanup_timer = threading.Timer(interval_seconds, _cleanup_and_reschedule)
            self._key_cleanup_timer.daemon = True
            self._key_cleanup_timer.start()

        self._key_cleanup_timer = threading.Timer(interval_seconds, _cleanup_and_reschedule)
        self._key_cleanup_timer.daemon = True
        self._key_cleanup_timer.start()

    def _check_jwt_key_rotation(self) -> None:
        from app.config.settings import settings
        jwt_keys = self._key_slots.get(KeySlot.JWT_SIGNING, {})
        for key_name, key_data in list(jwt_keys.items()):
            created_at = key_data.get("created_at")
            if created_at is None:
                continue
            if isinstance(created_at, datetime):
                age_days = (datetime.now(timezone.utc) - created_at).days
            else:
                continue
            if age_days >= settings.jwt_key_rotation_days:
                logger.info(
                    "hsm_jwt_key_auto_rotation",
                    key_name=key_name,
                    age_days=age_days,
                    rotation_days=settings.jwt_key_rotation_days,
                )
                self.rotate_key(key_name)

    def _check_desensitization_key_rotation(self) -> None:
        from app.config.settings import settings
        slots_to_check = [KeySlot.DESENSITIZATION_MAPPING, KeySlot.DATA_ENCRYPTION]
        for slot in slots_to_check:
            keys = self._key_slots.get(slot, {})
            for key_name, key_data in list(keys.items()):
                created_at = key_data.get("created_at")
                if created_at is None:
                    continue
                if isinstance(created_at, datetime):
                    age_days = (datetime.now(timezone.utc) - created_at).days
                else:
                    continue
                if age_days >= settings.desensitization_key_rotation_days:
                    logger.info(
                        "hsm_desensitization_key_auto_rotation",
                        key_name=key_name,
                        slot=slot.value,
                        age_days=age_days,
                        rotation_days=settings.desensitization_key_rotation_days,
                    )
                    self.rotate_key(key_name)

    def _check_hsm_availability(self) -> bool:
        if self._use_soft_hsm:
            logger.info("hsm_using_soft_hsm_mode")
            return True

        try:
            if self._is_pkcs11_available():
                return True

            if self._is_cloud_hsm_available():
                return True

            logger.warning("hsm_not_available_falling_back_to_soft_hsm")
            return False

        except Exception as e:
            logger.error("hsm_availability_check_failed", error=str(e))
            return False

    def _is_pkcs11_available(self) -> bool:
        try:
            pkcs11_lib_paths = [
                "/usr/lib/opensc/pkcs11.so",
                "/usr/lib64/pkcs11/opensc-pkcs11.so",
                "C:\\Windows\\System32\\opensc-pkcs11.dll",
            ]

            for lib_path in pkcs11_lib_paths:
                if os.path.exists(lib_path):
                    logger.info("hsm_pkcs11_library_found", path=lib_path)
                    return True

            soft_hsm_env = os.environ.get("SOFT_HSM_CONF", "")
            if soft_hsm_env and os.path.exists(os.path.dirname(soft_hsm_env)):
                logger.info("hsm_soft_hsm_configured", config=soft_hsm_env)
                return True

            return False

        except Exception as e:
            logger.debug("hsm_pkcs11_check_failed", error=str(e))
            return False

    def _is_cloud_hsm_available(self) -> bool:
        try:
            cloud_hsm_env_vars = [
                "AWS_CLOUDHSM_AWS_REGION",
                "AZURE_HSM_URI",
                "GCP_CLOUDHSM_PROJECT",
            ]

            for env_var in cloud_hsm_env_vars:
                if os.environ.get(env_var):
                    logger.info("hsm_cloud_hsm_env_detected", env_var=env_var)
                    return True

            return False

        except Exception as e:
            logger.debug("hsm_cloud_hsm_check_failed", error=str(e))
            return False

    def _generate_rsa_key_pair(self, key_size: int = 2048) -> tuple:
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=key_size,
            backend=default_backend()
        )
        public_key = private_key.public_key()
        return private_key, public_key

    def _serialize_key_pair(self, private_key: rsa.RSAPrivateKey, public_key: rsa.RSAPublicKey) -> tuple:
        encryption_key = self._get_key_encryption_password()
        if not encryption_key:
            logger.critical("hsm_private_key_no_password_refusing_serialization")
            raise RuntimeError(
                "HSM_PRIVATE_KEY_PASSWORD environment variable is not set. "
                "Private keys must be encrypted at rest. "
                "Set HSM_PRIVATE_KEY_PASSWORD before starting the service."
            )
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(encryption_key)
        )

        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )

        return private_pem, public_pem

    def _get_key_encryption_password(self) -> Optional[bytes]:
        password = os.environ.get("HSM_PRIVATE_KEY_PASSWORD")
        if password:
            return password.encode()
        return None

    def check_degraded_behavior(self, operation: str) -> bool:
        phase = self.get_degraded_phase()
        behavior = DEGRADED_BEHAVIORS[phase]
        op_map = {
            "jwt_signing": behavior.allow_jwt_signing,
            "data_encryption": behavior.allow_data_encryption,
            "desensitization_restore": behavior.allow_desensitization_restore,
            "emergency_signing": behavior.allow_emergency_signing,
        }
        return op_map.get(operation, False)

    def generate_key_pair(self, key_name: str, key_size: int = 2048,
                          slot: Optional[KeySlot] = None) -> Dict[str, Any]:
        if slot is None:
            slot = self._get_key_slot(key_name)

        with self._lock:
            private_key, public_key = self._generate_rsa_key_pair(key_size)
            private_pem, public_pem = self._serialize_key_pair(private_key, public_key)

            now = datetime.now(timezone.utc)
            self._key_slots[slot][key_name] = {
                'private_key': private_key,
                'public_key': public_key,
                'key_version': 1,
                'created_at': now,
                'expires_at': None,
                'is_active': True,
                'slot': slot,
            }

            logger.info("hsm_key_generated", key_name=key_name, key_version=1, slot=slot.value)

            self._persist_key_to_redis(key_name, private_pem, public_pem, 1, now, None, True, slot)

            return {
                'key_name': key_name,
                'key_version': 1,
                'public_key_pem': public_pem.decode('utf-8'),
                'slot': slot.value,
            }

    def sign(self, key_name: str, data: bytes) -> bytes:
        if self._use_soft_hsm and self._check_soft_hsm_expiry():
            raise RuntimeError("SoftHSM has expired (90 days). Signing is prohibited until HSM is reconfigured.")

        phase = self.get_degraded_phase()
        behavior = DEGRADED_BEHAVIORS[phase]

        slot = self._get_key_slot(key_name)
        if slot == KeySlot.EMERGENCY_SIGNING:
            if not behavior.allow_emergency_signing:
                raise RuntimeError(f"HSM in {phase.value} degraded phase: emergency signing is prohibited")
        else:
            if not behavior.allow_jwt_signing:
                raise RuntimeError(f"HSM in {phase.value} degraded phase: signing is prohibited")

        with self._lock:
            key_info, found_slot = self._find_key(key_name)
            if not key_info:
                raise ValueError(f"Key {key_name} not found")

            private_key = key_info['private_key']
            signature = private_key.sign(
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )

            logger.debug("hsm_sign_completed", key_name=key_name, data_len=len(data), degraded_phase=phase.value)
            return signature

    def verify_signature(self, key_name: str, data: bytes, signature: bytes) -> bool:
        if self._use_soft_hsm and self._check_soft_hsm_expiry():
            logger.warning("hsm_verify_on_expired_softhsm", key_name=key_name)
            raise RuntimeError("SoftHSM has expired (90 days). Verification is prohibited until HSM is reconfigured.")

        with self._lock:
            key_info, found_slot = self._find_key(key_name)
            if not key_info:
                raise ValueError(f"Key {key_name} not found")

            public_key = key_info['public_key']

            try:
                public_key.verify(
                    signature,
                    data,
                    padding.PSS(
                        mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH
                    ),
                    hashes.SHA256()
                )
                logger.debug("hsm_signature_verified", key_name=key_name)
                return True
            except InvalidSignature:
                slot_keys = self._key_slots.get(found_slot, {}) if found_slot else {}
                for cached_key_name, old_key_info in slot_keys.items():
                    if cached_key_name.startswith(f"{key_name}_v"):
                        try:
                            old_key_info['public_key'].verify(
                                signature,
                                data,
                                padding.PSS(
                                    mgf=padding.MGF1(hashes.SHA256()),
                                    salt_length=padding.PSS.MAX_LENGTH
                                ),
                                hashes.SHA256()
                            )
                            logger.debug("hsm_signature_verified_with_old_key",
                                        key_name=key_name,
                                        old_key_name=cached_key_name)
                            return True
                        except InvalidSignature:
                            continue
                logger.warning("hsm_signature_invalid", key_name=key_name)
                return False

    def encrypt(self, key_name: str, data: bytes) -> bytes:
        with self._lock:
            key_info, found_slot = self._find_key(key_name)
            if not key_info:
                raise ValueError(f"Key {key_name} not found")

            public_key = key_info['public_key']
            ciphertext = public_key.encrypt(
                data,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )

            logger.debug("hsm_encrypt_completed", key_name=key_name, data_len=len(data))
            return ciphertext

    def decrypt(self, key_name: str, ciphertext: bytes) -> bytes:
        with self._lock:
            key_info, found_slot = self._find_key(key_name)
            if not key_info:
                raise ValueError(f"Key {key_name} not found")

            private_key = key_info['private_key']
            plaintext = private_key.decrypt(
                ciphertext,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )

            logger.debug("hsm_decrypt_completed", key_name=key_name, data_len=len(ciphertext))
            return plaintext

    def get_public_key(self, key_name: str) -> Optional[str]:
        with self._lock:
            key_info, _ = self._find_key(key_name)
            if key_info:
                public_key = key_info['public_key']
                return public_key.public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo
                ).decode('utf-8')
            return None

    def get_private_key(self, key_name: str) -> Optional[str]:
        logger.critical("hsm_private_key_export_attempted", key_name=key_name)
        raise RuntimeError(
            "Private key export is prohibited by HSM security policy. "
            "Use sign() or sign_jwt() for signing operations instead."
        )

    def sign_jwt(self, key_name: str, header: Dict[str, Any], payload: Dict[str, Any], algorithm: str = "RS256") -> str:
        import base64
        import json as _json

        if algorithm != "RS256":
            logger.warning("hsm_jwt_algorithm_not_supported_forcing_rs256",
                           requested_algorithm=algorithm, forced_algorithm="RS256")
            algorithm = "RS256"

        def _json_serialize(obj: Any) -> str:
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

        if self._use_soft_hsm and self._check_soft_hsm_expiry():
            raise RuntimeError("SoftHSM has expired (90 days). JWT signing is prohibited until HSM is reconfigured.")

        phase = self.get_degraded_phase()
        behavior = DEGRADED_BEHAVIORS[phase]
        key_slot = self._get_key_slot(key_name)

        if key_slot == KeySlot.EMERGENCY_SIGNING:
            if not behavior.allow_emergency_signing:
                raise RuntimeError(f"HSM in {phase.value} degraded phase: emergency signing is prohibited")
        else:
            if not behavior.allow_jwt_signing:
                raise RuntimeError(f"HSM in {phase.value} degraded phase: JWT signing is prohibited")

        with self._lock:
            key_info, found_slot = self._find_key(key_name)
            if not key_info:
                raise ValueError(f"Key {key_name} not found")

            private_key = key_info['private_key']

            header_b64 = base64.urlsafe_b64encode(
                _json.dumps(header, separators=(',', ':'), default=_json_serialize).encode()
            ).rstrip(b'=').decode('utf-8')

            payload_b64 = base64.urlsafe_b64encode(
                _json.dumps(payload, separators=(',', ':'), default=_json_serialize).encode()
            ).rstrip(b'=').decode('utf-8')

            signing_input = f"{header_b64}.{payload_b64}".encode('utf-8')

            signature = private_key.sign(
                signing_input,
                padding.PKCS1v15(),
                hashes.SHA256()
            )

            signature_b64 = base64.urlsafe_b64encode(signature).rstrip(b'=').decode('utf-8')

            return f"{header_b64}.{payload_b64}.{signature_b64}"

    def rotate_key(self, key_name: str, parallel_days: int = 7) -> Dict[str, Any]:
        with self._lock:
            slot = self._get_key_slot(key_name)
            old_key_info = self._key_slots[slot].get(key_name)
            new_version = 1

            if old_key_info:
                new_version = old_key_info['key_version'] + 1
                old_key_info['expires_at'] = datetime.now(timezone.utc) + timedelta(days=parallel_days)
                old_key_info['is_active'] = False

            private_key, public_key = self._generate_rsa_key_pair()
            private_pem, public_pem = self._serialize_key_pair(private_key, public_key)

            now = datetime.now(timezone.utc)
            new_key_info = {
                'private_key': private_key,
                'public_key': public_key,
                'key_version': new_version,
                'created_at': now,
                'expires_at': None,
                'is_active': True,
                'slot': slot,
            }

            if old_key_info:
                old_private_pem, old_public_pem = self._serialize_key_pair(
                    old_key_info['private_key'], old_key_info['public_key']
                )
                self._persist_key_to_redis(
                    f"{key_name}_v{old_key_info['key_version']}",
                    old_private_pem,
                    old_public_pem,
                    old_key_info['key_version'],
                    old_key_info['created_at'],
                    old_key_info['expires_at'],
                    False,
                    slot,
                )
                if 'versions' not in old_key_info:
                    old_key_info['versions'] = []
                old_key_info['versions'].append(old_key_info.copy())
                self._key_slots[slot][f"{key_name}_v{old_key_info['key_version']}"] = old_key_info

            self._key_slots[slot][key_name] = new_key_info

            self._persist_key_to_redis(key_name, private_pem, public_pem, new_version, now, None, True, slot)

            logger.info("hsm_key_rotated", key_name=key_name, key_version=new_version, slot=slot.value)

            return {
                'key_name': key_name,
                'key_version': new_version,
                'public_key_pem': public_pem.decode('utf-8'),
                'slot': slot.value,
            }

    def cleanup_expired_keys(self) -> int:
        with self._lock:
            now = datetime.now(timezone.utc)
            expired_keys: List[Tuple[str, KeySlot]] = []

            for slot, keys in self._key_slots.items():
                for key_name, key_info in keys.items():
                    if key_info.get('expires_at') and now > key_info['expires_at']:
                        expired_keys.append((key_name, slot))

            for key_name, slot in expired_keys:
                del self._key_slots[slot][key_name]

            if expired_keys:
                logger.info("hsm_expired_keys_cleaned", count=len(expired_keys))
                self._safe_run_async_task(self._cleanup_redis_keys(expired_keys))

            return len(expired_keys)

    async def _cleanup_redis_keys(self, expired_keys: List[Tuple[str, KeySlot]]) -> None:
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey
            redis_client = await RedisPool.get_client()
            async with redis_client.pipeline(transaction=False) as pipe:
                for key_name, slot in expired_keys:
                    redis_key = RedisKey.hsm_key(slot.value, key_name)
                    pipe.delete(redis_key)
                    pipe.delete(RedisKey.hsm_key_private(slot.value, key_name))
                await pipe.execute()
            logger.info("hsm_expired_redis_keys_cleaned", count=len(expired_keys))
        except Exception as e:
            logger.error("failed_to_cleanup_redis_keys", error=str(e))

    def is_hsm_available(self) -> bool:
        return self._hsm_available

    def get_key_info(self, key_name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            key_info, found_slot = self._find_key(key_name)
            if key_info:
                return {
                    'key_name': key_name,
                    'key_version': key_info['key_version'],
                    'has_private_key': key_info.get('private_key') is not None,
                    'slot': found_slot.value if found_slot else None,
                }
            return None

    def list_keys(self) -> List[str]:
        with self._lock:
            result = []
            for slot, keys in self._key_slots.items():
                result.extend(keys.keys())
            return result

    def list_keys_by_slot(self, slot: KeySlot) -> List[str]:
        with self._lock:
            return list(self._key_slots[slot].keys())

    def is_degraded(self) -> bool:
        return self._is_degraded

    def get_degraded_at(self) -> Optional[datetime]:
        return self._degraded_at

    def get_degraded_phase(self) -> DegradedPhase:
        if not self._is_degraded or self._degraded_at is None:
            return DegradedPhase.NORMAL

        now = datetime.now(timezone.utc)
        time_since_degraded = now - self._degraded_at

        if time_since_degraded <= self.DEGRADED_PHASE_1_THRESHOLD:
            return DegradedPhase.CACHED
        elif time_since_degraded <= self.DEGRADED_PHASE_2_THRESHOLD:
            return DegradedPhase.RESTRICTED
        else:
            return DegradedPhase.EMERGENCY

    def get_hsm_mode(self) -> str:
        if self._use_soft_hsm:
            return "soft_hsm"
        return "hardware_hsm"

    def get_degradation_info(self) -> dict:
        info = {}
        if self._degraded_at:
            info["degraded_at"] = self._degraded_at.isoformat()
        if self._degraded_reason:
            info["degraded_reason"] = self._degraded_reason
        if self._soft_hsm_enabled_at:
            info["soft_hsm_enabled_at"] = self._soft_hsm_enabled_at.isoformat()
        return info

    def set_degraded(self, reason: str) -> None:
        with self._lock:
            if not self._is_degraded:
                self._is_degraded = True
                self._degraded_at = datetime.now(timezone.utc)
                self._degraded_reason = reason
                logger.error(
                    "hsm_entered_degraded_mode",
                    reason=reason,
                    degraded_at=self._degraded_at.isoformat()
                )
                self._persist_degraded_state()

    def clear_degraded(self) -> None:
        with self._lock:
            if self._is_degraded:
                logger.info(
                    "hsm_exited_degraded_mode",
                    duration=(datetime.now(timezone.utc) - self._degraded_at).total_seconds() if self._degraded_at else 0
                )
                self._is_degraded = False
                self._degraded_at = None
                self._degraded_reason = None
                self._persist_degraded_state()

    def _persist_degraded_state(self) -> None:
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey
            import asyncio

            async def persist():
                redis_client = await RedisPool.get_client()
                if self._is_degraded:
                    ttl_seconds = 7 * 86400
                    async with redis_client.pipeline(transaction=True) as pipe:
                        await pipe.set(RedisKey.HSM_DEGRADED, "true", ex=ttl_seconds)
                        if self._degraded_at:
                            await pipe.set(RedisKey.HSM_DEGRADED_AT, self._degraded_at.isoformat(), ex=ttl_seconds)
                        if self._degraded_reason:
                            await pipe.set(RedisKey.HSM_DEGRADED_REASON, self._degraded_reason, ex=ttl_seconds)
                        await pipe.execute()
                else:
                    await redis_client.delete(RedisKey.HSM_DEGRADED)
                    await redis_client.delete(RedisKey.HSM_DEGRADED_AT)
                    await redis_client.delete(RedisKey.HSM_DEGRADED_REASON)

            self._safe_run_async_task(persist())
        except Exception as e:
            logger.error("failed_to_persist_degraded_state", error=str(e))

    def _restore_degraded_state_from_redis(self) -> None:
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey
            import asyncio

            async def restore():
                redis_client = await RedisPool.get_client()
                degraded_str = await redis_client.get(RedisKey.HSM_DEGRADED)
                if degraded_str == b"true":
                    self._is_degraded = True

                    degraded_at_str = await redis_client.get(RedisKey.HSM_DEGRADED_AT)
                    if degraded_at_str:
                        try:
                            self._degraded_at = datetime.fromisoformat(degraded_at_str.decode())
                        except Exception:
                            self._degraded_at = datetime.now(timezone.utc)

                    degraded_reason_str = await redis_client.get(RedisKey.HSM_DEGRADED_REASON)
                    if degraded_reason_str:
                        self._degraded_reason = degraded_reason_str.decode()

                    logger.warning(
                        "hsm_degraded_state_restored_from_redis",
                        degraded_at=self._degraded_at,
                        reason=self._degraded_reason
                    )

            self._safe_run_async_task(restore())
        except Exception as e:
            logger.error("failed_to_restore_degraded_state_from_redis", error=str(e))
        self._flush_pending_persistence()

    def _get_encryption_key(self) -> bytes:
        key_env = os.environ.get("HSM_KEY_ENCRYPTION_KEY")
        if not key_env:
            raise RuntimeError(
                "HSM_KEY_ENCRYPTION_KEY environment variable is not set. "
                "Refusing to use hardcoded encryption key. "
                "Set HSM_KEY_ENCRYPTION_KEY to a valid Fernet key before starting the service."
            )
        try:
            from cryptography.fernet import Fernet
            Fernet(key_env.encode() if isinstance(key_env, str) else key_env)
        except Exception as e:
            raise ValueError(
                f"HSM_KEY_ENCRYPTION_KEY is not a valid Fernet key. "
                f"Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\". "
                f"Validation error: {e}"
            )
        return key_env.encode()

    def _persist_key_to_redis(self, key_name: str, private_pem: bytes, public_pem: bytes,
                              key_version: int, created_at: datetime, expires_at: Optional[datetime],
                              is_active: bool, slot: Optional[KeySlot] = None) -> None:
        if slot is None:
            slot = self._get_key_slot(key_name)

        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey
            import asyncio

            async def persist():
                redis_client = await RedisPool.get_client()

                key_data = {
                    'key_name': key_name,
                    'key_version': key_version,
                    'public_key_pem': public_pem.decode('utf-8'),
                    'created_at': created_at.isoformat(),
                    'expires_at': expires_at.isoformat() if expires_at else None,
                    'is_active': is_active,
                    'slot': slot.value,
                }

                redis_key = RedisKey.hsm_key(slot.value, key_name)
                await redis_client.set(redis_key, json.dumps(key_data), ex=86400*365)

                fernet = Fernet(self._get_encryption_key())
                encrypted_private_key = fernet.encrypt(private_pem)
                await redis_client.set(RedisKey.hsm_key_private(slot.value, key_name), encrypted_private_key, ex=86400*365)

                logger.debug("hsm_key_persisted_to_redis", key_name=key_name, key_version=key_version, slot=slot.value)

            self._safe_run_async_task(persist())
        except Exception as e:
            logger.error("failed_to_persist_key_to_redis", key_name=key_name, error=str(e))

    def _restore_keys_from_redis(self) -> None:
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey
            import asyncio

            async def restore():
                redis_client = await RedisPool.get_client()

                async for key in redis_client.scan_iter(RedisKey.HSM_KEYS_PATTERN):
                    key_str = key.decode()
                    private_suffix = ":private"
                    raw_key_name = key_str.replace(":hsm:keys:", "").replace(private_suffix, "")

                    slot = None
                    key_name = raw_key_name
                    for slot_enum in KeySlot:
                        prefix = f"{slot_enum.value}:"
                        if raw_key_name.startswith(prefix):
                            slot = slot_enum
                            key_name = raw_key_name[len(prefix):]
                            break

                    if slot is None:
                        slot = self._get_key_slot(key_name)

                    redis_key = RedisKey.hsm_key(slot.value, key_name)
                    meta_data_str = await redis_client.get(redis_key)
                    encrypted_private_key = await redis_client.get(key)

                    if meta_data_str and encrypted_private_key:
                        try:
                            meta_data = json.loads(meta_data_str.decode())

                            fernet = Fernet(self._get_encryption_key())
                            private_pem = fernet.decrypt(encrypted_private_key)

                            password = self._get_key_encryption_password()
                            private_key = serialization.load_pem_private_key(
                                private_pem,
                                password=password,
                                backend=default_backend()
                            )
                            public_key = serialization.load_pem_public_key(
                                meta_data['public_key_pem'].encode(),
                                backend=default_backend()
                            )

                            created_at = datetime.fromisoformat(meta_data['created_at'])
                            expires_at = datetime.fromisoformat(meta_data['expires_at']) if meta_data.get('expires_at') else None

                            self._key_slots[slot][key_name] = {
                                'private_key': private_key,
                                'public_key': public_key,
                                'key_version': meta_data['key_version'],
                                'created_at': created_at,
                                'expires_at': expires_at,
                                'is_active': meta_data.get('is_active', True),
                                'slot': slot,
                            }

                            logger.info("hsm_key_restored_from_redis", key_name=key_name, key_version=meta_data['key_version'], slot=slot.value)
                        except Exception as e:
                            logger.error("failed_to_restore_key_from_redis", key_name=key_name, error=str(e))

            self._safe_run_async_task(restore())
        except Exception as e:
            logger.error("failed_to_restore_keys_from_redis", error=str(e))
        self._flush_pending_persistence()

    def _migrate_legacy_keys(self) -> None:
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey, LegacyRedisKey
            import asyncio

            async def migrate():
                redis_client = await RedisPool.get_client()

                async for key in redis_client.scan_iter(LegacyRedisKey.HSM_KEYS_PATTERN):
                    raw_key_name = key.decode().replace("hsm:keys:", "").replace(":private", "")

                    has_slot_prefix = any(raw_key_name.startswith(f"{s.value}:") for s in KeySlot)
                    if has_slot_prefix:
                        continue

                    meta_key = LegacyRedisKey.hsm_key("", raw_key_name)
                    meta_data_str = await redis_client.get(meta_key)
                    encrypted_private_key = await redis_client.get(key)

                    if meta_data_str and encrypted_private_key:
                        try:
                            meta_data = json.loads(meta_data_str.decode())
                            key_name = raw_key_name
                            slot = self._get_key_slot(key_name)

                            new_meta_key = RedisKey.hsm_key(slot.value, key_name)
                            new_private_key = RedisKey.hsm_key_private(slot.value, key_name)

                            updated_data = dict(meta_data)
                            updated_data['slot'] = slot.value

                            ttl = await redis_client.ttl(meta_key)
                            if ttl < 0:
                                ttl = 86400 * 365

                            await redis_client.set(new_meta_key, json.dumps(updated_data), ex=ttl)
                            await redis_client.set(new_private_key, encrypted_private_key, ex=ttl)
                            await redis_client.delete(meta_key)
                            await redis_client.delete(key)

                            logger.info("hsm_legacy_key_migrated", key_name=key_name, slot=slot.value)
                        except Exception as e:
                            logger.error("failed_to_migrate_legacy_key", key_name=raw_key_name, error=str(e))

            self._safe_run_async_task(migrate())
        except Exception as e:
            logger.error("failed_to_migrate_legacy_keys", error=str(e))


hsm_manager = HSMManager()
