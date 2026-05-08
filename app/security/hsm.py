from typing import Optional, Dict, Any, List
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature
import os
import structlog
import threading
import subprocess
import re

logger = structlog.get_logger()

class HSMManager:
    def __init__(self) -> None:
        self._use_soft_hsm: bool = os.environ.get("USE_SOFT_HSM", "false").lower() == "true"
        self._hsm_available: bool = self._check_hsm_availability()
        self._cached_keys: Dict[str, Dict[str, Any]] = {}
        self._lock: threading.RLock = threading.RLock()

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
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )

        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )

        return private_pem, public_pem

    def generate_key_pair(self, key_name: str, key_size: int = 2048) -> Dict[str, Any]:
        with self._lock:
            private_key, public_key = self._generate_rsa_key_pair(key_size)
            private_pem, public_pem = self._serialize_key_pair(private_key, public_key)

            self._cached_keys[key_name] = {
                'private_key': private_key,
                'public_key': public_key,
                'key_version': 1
            }

            logger.info("hsm_key_generated", key_name=key_name, key_version=1)

            return {
                'key_name': key_name,
                'key_version': 1,
                'public_key_pem': public_pem.decode('utf-8')
            }

    def sign(self, key_name: str, data: bytes) -> bytes:
        with self._lock:
            key_info = self._cached_keys.get(key_name)
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

            logger.debug("hsm_sign_completed", key_name=key_name, data_len=len(data))
            return signature

    def verify_signature(self, key_name: str, data: bytes, signature: bytes) -> bool:
        with self._lock:
            key_info = self._cached_keys.get(key_name)
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
                logger.warning("hsm_signature_invalid", key_name=key_name)
                return False

    def encrypt(self, key_name: str, data: bytes) -> bytes:
        with self._lock:
            key_info = self._cached_keys.get(key_name)
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
            key_info = self._cached_keys.get(key_name)
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
            key_info = self._cached_keys.get(key_name)
            if key_info:
                public_key = key_info['public_key']
                return public_key.public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo
                ).decode('utf-8')
            return None

    def rotate_key(self, key_name: str) -> Dict[str, Any]:
        with self._lock:
            key_info = self._cached_keys.get(key_name)
            new_version = 1

            if key_info:
                new_version = key_info['key_version'] + 1

            private_key, public_key = self._generate_rsa_key_pair()
            private_pem, public_pem = self._serialize_key_pair(private_key, public_key)

            self._cached_keys[key_name] = {
                'private_key': private_key,
                'public_key': public_key,
                'key_version': new_version
            }

            logger.info("hsm_key_rotated", key_name=key_name, key_version=new_version)

            return {
                'key_name': key_name,
                'key_version': new_version,
                'public_key_pem': public_pem.decode('utf-8')
            }

    def is_hsm_available(self) -> bool:
        return self._hsm_available

    def get_key_info(self, key_name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            key_info = self._cached_keys.get(key_name)
            if key_info:
                return {
                    'key_name': key_name,
                    'key_version': key_info['key_version'],
                    'has_private_key': key_info.get('private_key') is not None
                }
            return None

    def list_keys(self) -> List[str]:
        with self._lock:
            return list(self._cached_keys.keys())

hsm_manager = HSMManager()