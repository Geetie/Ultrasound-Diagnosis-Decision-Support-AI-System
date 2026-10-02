"""HSM 密钥管理器。

负责密钥的生命周期管理，包括生成、存储、查找、轮换和清理。
从 HSMManager 中提取，实现单一职责原则。
"""

import threading
from typing import Any

import structlog
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.security.hsm.enums import KeySlot, get_key_slot
from app.security.hsm.persistence_manager import PersistenceManager

logger = structlog.get_logger()


class KeyManager:
    """HSM 密钥管理器。

    管理密钥的完整生命周期：
    - RSA 密钥对生成和序列化
    - 密钥存储（内存 + Redis 持久化）
    - 密钥查找（按名称和槽位）
    - 密钥轮换（旧密钥保留并行期）
    - 过期密钥清理

    线程安全：通过 RLock 保护密钥槽位操作。

    Args:
        persistence_manager: 持久化管理器，用于密钥的 Redis 存储
    """

    KEY_CLEANUP_INTERVAL_HOURS = 24

    def __init__(self, persistence_manager: PersistenceManager) -> None:
        self._key_slots: dict[KeySlot, dict[str, dict[str, Any]]] = {
            slot: {} for slot in KeySlot
        }
        self._lock = threading.RLock()
        self._persistence_manager = persistence_manager

    @property
    def key_slots(self) -> dict[KeySlot, dict[str, dict[str, Any]]]:
        """密钥槽位字典（用于向后兼容测试访问）。"""
        return self._key_slots

    @property
    def lock(self) -> threading.RLock:
        """线程锁（用于向后兼容测试访问）。"""
        return self._lock

    def find_key(self, key_name: str) -> tuple[dict[str, Any] | None, KeySlot | None]:
        """查找密钥信息。

        首先在密钥名称对应的默认槽位中查找，
        如果找不到则在所有槽位中搜索。

        Args:
            key_name: 密钥名称

        Returns:
            Tuple[Optional[Dict], Optional[KeySlot]]: 密钥信息和槽位
        """
        slot = get_key_slot(key_name)
        key_info = self._key_slots[slot].get(key_name)
        if key_info:
            return key_info, slot
        for s, keys in self._key_slots.items():
            if key_name in keys:
                return keys[key_name], s
        return None, None

    def generate_key_pair(
        self,
        key_name: str,
        key_size: int = 2048,
        slot: KeySlot | None = None,
    ) -> dict[str, Any]:
        """生成 RSA 密钥对。

        生成密钥对后存储到内存槽位和 Redis 持久化。

        Args:
            key_name: 密钥名称
            key_size: RSA 密钥大小（默认2048位）
            slot: 密钥槽位（默认根据名称前缀自动路由）

        Returns:
            dict: 包含 key_name, key_version, public_key_pem, slot 的字典
        """
        from datetime import datetime, timezone

        if slot is None:
            slot = get_key_slot(key_name)

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

            self._persistence_manager.persist_key(
                key_name, private_pem, public_pem, 1, now, None, True, slot,
                encryption_key_getter=self._get_encryption_key,
            )

            return {
                'key_name': key_name,
                'key_version': 1,
                'public_key_pem': public_pem.decode('utf-8'),
                'slot': slot.value,
            }

    def rotate_key(self, key_name: str, parallel_days: int = 7) -> dict[str, Any]:
        """轮换密钥。

        生成新的密钥对，旧密钥保留 parallel_days 天的并行期。
        旧密钥会被重命名为 {key_name}_v{version}。

        Args:
            key_name: 密钥名称
            parallel_days: 旧密钥并行保留天数（默认7天）

        Returns:
            dict: 新密钥信息
        """
        from datetime import datetime, timedelta, timezone

        with self._lock:
            slot = get_key_slot(key_name)
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
                self._persistence_manager.persist_key(
                    f"{key_name}_v{old_key_info['key_version']}",
                    old_private_pem,
                    old_public_pem,
                    old_key_info['key_version'],
                    old_key_info['created_at'],
                    old_key_info['expires_at'],
                    False,
                    slot,
                    encryption_key_getter=self._get_encryption_key,
                )
                if 'versions' not in old_key_info:
                    old_key_info['versions'] = []
                old_key_info['versions'].append(old_key_info.copy())
                self._key_slots[slot][f"{key_name}_v{old_key_info['key_version']}"] = old_key_info

            self._key_slots[slot][key_name] = new_key_info

            self._persistence_manager.persist_key(
                key_name, private_pem, public_pem, new_version, now, None, True, slot,
                encryption_key_getter=self._get_encryption_key,
            )

            logger.info("hsm_key_rotated", key_name=key_name, key_version=new_version, slot=slot.value)

            return {
                'key_name': key_name,
                'key_version': new_version,
                'public_key_pem': public_pem.decode('utf-8'),
                'slot': slot.value,
            }

    def cleanup_expired_keys(self) -> int:
        """清理过期的密钥。

        扫描所有槽位中的密钥，删除已过期的密钥，
        同时从 Redis 中删除对应的持久化数据。

        Returns:
            int: 清理的密钥数量
        """
        from datetime import datetime, timezone

        with self._lock:
            now = datetime.now(timezone.utc)
            expired_keys: list[tuple[str, KeySlot]] = []

            for slot, keys in self._key_slots.items():
                for key_name, key_info in keys.items():
                    if key_info.get('expires_at') and now > key_info['expires_at']:
                        expired_keys.append((key_name, slot))

            for key_name, slot in expired_keys:
                del self._key_slots[slot][key_name]

            if expired_keys:
                logger.info("hsm_expired_keys_cleaned", count=len(expired_keys))
                self._persistence_manager.safe_run_async_task(
                    self._persistence_manager.cleanup_redis_keys(expired_keys)
                )

            return len(expired_keys)

    def get_key_info(self, key_name: str) -> dict[str, Any] | None:
        """获取密钥信息。

        Args:
            key_name: 密钥名称

        Returns:
            Optional[dict]: 密钥信息字典，包含 key_name, key_version, has_private_key, slot
        """
        with self._lock:
            key_info, found_slot = self.find_key(key_name)
            if key_info:
                return {
                    'key_name': key_name,
                    'key_version': key_info['key_version'],
                    'has_private_key': key_info.get('private_key') is not None,
                    'slot': found_slot.value if found_slot else None,
                }
            return None

    def list_keys(self) -> list[str]:
        """列出所有密钥名称。

        Returns:
            List[str]: 密钥名称列表
        """
        with self._lock:
            result = []
            for slot, keys in self._key_slots.items():
                result.extend(keys.keys())
            return result

    def list_keys_by_slot(self, slot: KeySlot) -> list[str]:
        """列出指定槽位的密钥名称。

        Args:
            slot: 密钥槽位

        Returns:
            List[str]: 密钥名称列表
        """
        with self._lock:
            return list(self._key_slots[slot].keys())

    def get_public_key(self, key_name: str) -> str | None:
        """获取密钥的公钥 PEM 字符串。

        Args:
            key_name: 密钥名称

        Returns:
            Optional[str]: PEM 编码的公钥字符串
        """
        with self._lock:
            key_info, _ = self.find_key(key_name)
            if key_info:
                public_key = key_info['public_key']
                return public_key.public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo
                ).decode('utf-8')
            return None

    def check_jwt_key_rotation(self, rotation_days: int) -> None:
        """检查并执行 JWT 密钥的自动轮换。

        Args:
            rotation_days: 密钥轮换周期（天）
        """
        from datetime import datetime, timezone

        with self._lock:
            jwt_keys = self._key_slots.get(KeySlot.JWT_SIGNING, {})
            for key_name, key_data in list(jwt_keys.items()):
                created_at = key_data.get("created_at")
                if created_at is None:
                    continue
                if isinstance(created_at, datetime):
                    age_days = (datetime.now(timezone.utc) - created_at).days
                else:
                    continue
                if age_days >= rotation_days:
                    logger.info(
                        "hsm_jwt_key_auto_rotation",
                        key_name=key_name,
                        age_days=age_days,
                        rotation_days=rotation_days,
                    )
                    self.rotate_key(key_name)

    def check_desensitization_key_rotation(self, rotation_days: int) -> None:
        """检查并执行脱敏密钥的自动轮换。

        Args:
            rotation_days: 密钥轮换周期（天）
        """
        from datetime import datetime, timezone

        with self._lock:
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
                    if age_days >= rotation_days:
                        logger.info(
                            "hsm_desensitization_key_auto_rotation",
                            key_name=key_name,
                            slot=slot.value,
                            age_days=age_days,
                            rotation_days=rotation_days,
                        )
                        self.rotate_key(key_name)

    def _generate_rsa_key_pair(self, key_size: int = 2048) -> tuple:
        """生成 RSA 密钥对。

        Args:
            key_size: 密钥大小（位）

        Returns:
            tuple: (private_key, public_key)
        """
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=key_size,
            backend=default_backend()
        )
        public_key = private_key.public_key()
        return private_key, public_key

    def _serialize_key_pair(self, private_key, public_key) -> tuple:
        """序列化密钥对为 PEM 格式。

        私钥使用密码加密存储。

        Args:
            private_key: RSA 私钥对象
            public_key: RSA 公钥对象

        Returns:
            tuple: (private_pem, public_pem)

        Raises:
            RuntimeError: 如果 HSM_PRIVATE_KEY_PASSWORD 未设置
        """
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

    @staticmethod
    def _get_key_encryption_password() -> bytes | None:
        """获取密钥加密密码。

        Returns:
            Optional[bytes]: 密码字节串
        """
        import os
        password = os.environ.get("HSM_PRIVATE_KEY_PASSWORD")
        if password:
            return password.encode()
        return None

    @staticmethod
    def _get_encryption_key() -> bytes:
        """获取 Fernet 加密密钥。

        Returns:
            bytes: Fernet 加密密钥

        Raises:
            RuntimeError: 如果 HSM_KEY_ENCRYPTION_KEY 未设置
            ValueError: 如果密钥不是有效的 Fernet 密钥
        """
        import os

        from cryptography.fernet import Fernet as _Fernet

        key_env = os.environ.get("HSM_KEY_ENCRYPTION_KEY")
        if not key_env:
            raise RuntimeError(
                "HSM_KEY_ENCRYPTION_KEY environment variable is not set. "
                "Refusing to use hardcoded encryption key. "
                "Set HSM_KEY_ENCRYPTION_KEY to a valid Fernet key before starting the service."
            )
        try:
            _Fernet(key_env.encode() if isinstance(key_env, str) else key_env)
        except Exception as e:
            raise ValueError(
                f"HSM_KEY_ENCRYPTION_KEY is not a valid Fernet key. "
                f"Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\". "
                f"Validation error: {e}"
            )
        return key_env.encode()
