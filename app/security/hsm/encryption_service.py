"""HSM 加密服务。

负责加密操作，包括签名、验签、加密和解密。
从 HSMManager 中提取，实现单一职责原则。
"""

import base64
from typing import Any

import structlog
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from app.security.hsm.degradation_manager import DegradationManager
from app.security.hsm.enums import DEGRADED_BEHAVIORS, KeySlot, get_key_slot
from app.security.hsm.key_manager import KeyManager
from app.security.hsm.soft_hsm_manager import SoftHSMManager

logger = structlog.get_logger()


class EncryptionService:
    """HSM 加密服务。

    提供基于 RSA 密钥的加密操作：
    - 数据签名（PSS 填充）
    - 签名验证（支持轮换密钥回退）
    - 数据加密（OAEP 填充）
    - 数据解密（OAEP 填充）
    - JWT 签名（PKCS1v15 填充）

    所有操作在执行前会检查降级状态和 SoftHSM 过期状态。

    Args:
        key_manager: 密钥管理器，提供密钥查找
        degradation_manager: 降级管理器，提供操作权限检查
        soft_hsm_manager: SoftHSM 管理器，提供过期检查
    """

    def __init__(
        self,
        key_manager: KeyManager,
        degradation_manager: DegradationManager,
        soft_hsm_manager: SoftHSMManager,
    ) -> None:
        self._key_manager = key_manager
        self._degradation_manager = degradation_manager
        self._soft_hsm_manager = soft_hsm_manager

    def sign(self, key_name: str, data: bytes) -> bytes:
        """使用指定密钥签名数据。

        签名前检查：
        1. SoftHSM 是否过期
        2. 当前降级阶段是否允许签名操作

        Args:
            key_name: 密钥名称
            data: 待签名数据

        Returns:
            bytes: 签名结果

        Raises:
            RuntimeError: SoftHSM 已过期或降级阶段不允许签名
            ValueError: 密钥不存在
        """
        if self._soft_hsm_manager.is_enabled and self._soft_hsm_manager.is_expired():
            raise RuntimeError("SoftHSM has expired (90 days). Signing is prohibited until HSM is reconfigured.")

        phase = self._degradation_manager.get_degraded_phase()
        behavior = DEGRADED_BEHAVIORS[phase]

        slot = get_key_slot(key_name)
        if slot == KeySlot.EMERGENCY_SIGNING:
            if not behavior.allow_emergency_signing:
                raise RuntimeError(f"HSM in {phase.value} degraded phase: emergency signing is prohibited")
        else:
            if not behavior.allow_jwt_signing:
                raise RuntimeError(f"HSM in {phase.value} degraded phase: signing is prohibited")

        with self._key_manager.lock:
            key_info, found_slot = self._key_manager.find_key(key_name)
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

            logger.debug("hsm_sign_completed", key_name=key_name, data_len=len(data),
                         degraded_phase=phase.value)
            return signature

    def verify_signature(self, key_name: str, data: bytes, signature: bytes) -> bool:
        """验证签名。

        如果主密钥验证失败，会尝试使用同一槽位中的轮换旧密钥验证。

        Args:
            key_name: 密钥名称
            data: 原始数据
            signature: 待验证的签名

        Returns:
            bool: 签名是否有效

        Raises:
            RuntimeError: SoftHSM 已过期
            ValueError: 密钥不存在
        """
        if self._soft_hsm_manager.is_enabled and self._soft_hsm_manager.is_expired():
            logger.warning("hsm_verify_on_expired_softhsm", key_name=key_name)
            raise RuntimeError("SoftHSM has expired (90 days). Verification is prohibited until HSM is reconfigured.")

        with self._key_manager.lock:
            key_info, found_slot = self._key_manager.find_key(key_name)
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
                # 尝试使用轮换旧密钥验证
                slot_keys = self._key_manager.key_slots.get(found_slot, {}) if found_slot else {}
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
        """使用指定密钥加密数据。

        加密前检查降级阶段是否允许数据加密操作。

        Args:
            key_name: 密钥名称
            data: 待加密数据

        Returns:
            bytes: 加密结果

        Raises:
            RuntimeError: 降级阶段不允许加密
            ValueError: 密钥不存在
        """
        if not self._degradation_manager.check_degraded_behavior("data_encryption"):
            raise RuntimeError(f"HSM in {self._degradation_manager.get_degraded_phase().value} phase: encryption is prohibited")

        with self._key_manager.lock:
            key_info, found_slot = self._key_manager.find_key(key_name)
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
        """使用指定密钥解密数据。

        解密前检查降级阶段是否允许脱敏还原操作。

        Args:
            key_name: 密钥名称
            ciphertext: 待解密的密文

        Returns:
            bytes: 解密结果

        Raises:
            RuntimeError: 降级阶段不允许解密
            ValueError: 密钥不存在
        """
        if not self._degradation_manager.check_degraded_behavior("desensitization_restore"):
            raise RuntimeError(f"HSM in {self._degradation_manager.get_degraded_phase().value} phase: decryption is prohibited")

        with self._key_manager.lock:
            key_info, found_slot = self._key_manager.find_key(key_name)
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

    def sign_jwt(self, key_name: str, header: dict[str, Any],
                 payload: dict[str, Any], algorithm: str = "RS256") -> str:
        """使用指定密钥签名 JWT。

        JWT 签名使用 PKCS1v15 填充（标准 JWT 格式）。
        签名前检查 SoftHSM 过期状态和降级阶段权限。

        Args:
            key_name: 密钥名称
            header: JWT 头部
            payload: JWT 载荷
            algorithm: 签名算法（仅支持 RS256）

        Returns:
            str: 编码后的 JWT 字符串

        Raises:
            RuntimeError: SoftHSM 已过期或降级阶段不允许签名
            ValueError: 密钥不存在
        """
        import json as _json
        from datetime import datetime

        if algorithm != "RS256":
            logger.warning("hsm_jwt_algorithm_not_supported_forcing_rs256",
                           requested_algorithm=algorithm, forced_algorithm="RS256")
            algorithm = "RS256"

        def _json_serialize(obj: Any) -> str:
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

        if self._soft_hsm_manager.is_enabled and self._soft_hsm_manager.is_expired():
            raise RuntimeError("SoftHSM has expired (90 days). JWT signing is prohibited until HSM is reconfigured.")

        phase = self._degradation_manager.get_degraded_phase()
        behavior = DEGRADED_BEHAVIORS[phase]
        key_slot = get_key_slot(key_name)

        if key_slot == KeySlot.EMERGENCY_SIGNING:
            if not behavior.allow_emergency_signing:
                raise RuntimeError(f"HSM in {phase.value} degraded phase: emergency signing is prohibited")
        else:
            if not behavior.allow_jwt_signing:
                raise RuntimeError(f"HSM in {phase.value} degraded phase: JWT signing is prohibited")

        with self._key_manager.lock:
            key_info, found_slot = self._key_manager.find_key(key_name)
            if not key_info:
                raise ValueError(f"Key {key_name} not found")

            private_key = key_info['private_key']

            header_b64 = base64.urlsafe_b64encode(
                _json.dumps(header, separators=(',', ':'), default=_json_serialize).encode()
            ).rstrip(b'=').decode('utf-8')

            payload_b64 = base64.urlsafe_b64encode(
                _json.dumps(payload, separators=(',', ':'), default=_json_serialize).encode()
            ).rstrip(b'=').decode('utf-8')

            signing_input = f"{header_b64}.{payload_b64}".encode()

            signature = private_key.sign(
                signing_input,
                padding.PKCS1v15(),
                hashes.SHA256()
            )

            signature_b64 = base64.urlsafe_b64encode(signature).rstrip(b'=').decode('utf-8')

            return f"{header_b64}.{payload_b64}.{signature_b64}"

    @staticmethod
    def get_private_key(key_name: str) -> str:
        """禁止导出私钥。

        安全策略：私钥不得离开 HSM，只能通过 sign/sign_jwt 使用。

        Raises:
            RuntimeError: 始终抛出
        """
        logger.critical("hsm_private_key_export_attempted", key_name=key_name)
        raise RuntimeError(
            "Private key export is prohibited by HSM security policy. "
            "Use sign() or sign_jwt() for signing operations instead."
        )
