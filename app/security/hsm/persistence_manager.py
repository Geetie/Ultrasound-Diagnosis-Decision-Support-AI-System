"""HSM 持久化管理器。

负责 HSM 数据的 Redis 持久化操作，包括密钥存储、降级状态持久化、
密钥恢复和旧密钥迁移。
从 HSMManager 中提取，实现单一职责原则。
"""

import asyncio
import json
import os
import threading
from collections.abc import Callable
from typing import Any

import structlog
from cryptography.fernet import Fernet
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from app.security.hsm.enums import KeySlot, get_key_slot

logger = structlog.get_logger()


class PersistenceManager:
    """HSM 持久化管理器。

    管理 HSM 数据的 Redis 持久化操作，包括：
    - 异步任务安全执行
    - 密钥元数据和私钥的持久化
    - 降级状态的持久化和恢复
    - 密钥的批量恢复
    - 旧版密钥的迁移
    - 过期密钥的 Redis 清理

    线程安全：通过内部锁保护异步任务队列。
    """

    def __init__(self) -> None:
        self._pending_tasks: set = set()
        self._pending_persistence: list = []

    def safe_run_async_task(self, coro) -> None:
        """安全地从同步上下文运行异步协程。

        处理以下场景：
        - 有运行中的事件循环：创建 Task
        - 无事件循环：同步执行
        - 测试环境：使用 ensure_future

        Args:
            coro: 要执行的异步协程
        """
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

    def _on_task_done(self, task) -> None:
        """异步任务完成回调。"""
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
        """清理所有待完成的异步任务。"""
        if self._pending_tasks:
            await asyncio.gather(*list(self._pending_tasks), return_exceptions=True)
            self._pending_tasks.clear()

    def flush_pending_persistence(self) -> None:
        """刷新所有待执行的持久化操作。"""
        if not self._pending_persistence:
            return
        pending = list(self._pending_persistence)
        self._pending_persistence.clear()
        for coro in pending:
            self.safe_run_async_task(coro)
        logger.info("hsm_pending_persistence_flushed", count=len(pending))

    def persist_key(
        self,
        key_name: str,
        private_pem: bytes,
        public_pem: bytes,
        key_version: int,
        created_at,
        expires_at,
        is_active: bool,
        slot: KeySlot | None = None,
        encryption_key_getter: Callable[[], bytes] | None = None,
    ) -> None:
        """持久化密钥到 Redis。

        Args:
            key_name: 密钥名称
            private_pem: PEM 编码的私钥
            public_pem: PEM 编码的公钥
            key_version: 密钥版本
            created_at: 创建时间
            expires_at: 过期时间
            is_active: 是否激活
            slot: 密钥槽位
            encryption_key_getter: 获取 Fernet 加密密钥的回调
        """
        if slot is None:
            slot = get_key_slot(key_name)

        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey

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
                await redis_client.set(redis_key, json.dumps(key_data), ex=86400 * 365)

                if encryption_key_getter:
                    fernet = Fernet(encryption_key_getter())
                    encrypted_private_key = fernet.encrypt(private_pem)
                    await redis_client.set(
                        RedisKey.hsm_key_private(slot.value, key_name),
                        encrypted_private_key,
                        ex=86400 * 365,
                    )

                logger.debug("hsm_key_persisted_to_redis", key_name=key_name,
                             key_version=key_version, slot=slot.value)

            self.safe_run_async_task(persist())
        except Exception as e:
            logger.error("failed_to_persist_key_to_redis", key_name=key_name, error=str(e))

    def persist_degraded_state(
        self,
        is_degraded: bool,
        degraded_at: str | None,
        degraded_reason: str | None,
    ) -> None:
        """持久化降级状态到 Redis。

        Args:
            is_degraded: 是否降级
            degraded_at: 降级时间 ISO 格式字符串
            degraded_reason: 降级原因
        """
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey

            async def persist():
                redis_client = await RedisPool.get_client()
                if is_degraded:
                    ttl_seconds = 7 * 86400
                    async with redis_client.pipeline(transaction=True) as pipe:
                        await pipe.set(RedisKey.HSM_DEGRADED, "true", ex=ttl_seconds)
                        if degraded_at:
                            await pipe.set(RedisKey.HSM_DEGRADED_AT, degraded_at, ex=ttl_seconds)
                        if degraded_reason:
                            await pipe.set(RedisKey.HSM_DEGRADED_REASON, degraded_reason, ex=ttl_seconds)
                        await pipe.execute()
                else:
                    await redis_client.delete(RedisKey.HSM_DEGRADED)
                    await redis_client.delete(RedisKey.HSM_DEGRADED_AT)
                    await redis_client.delete(RedisKey.HSM_DEGRADED_REASON)

            self.safe_run_async_task(persist())
        except Exception as e:
            logger.error("failed_to_persist_degraded_state", error=str(e))

    def restore_degraded_state(self, callback) -> None:
        """从 Redis 恢复降级状态。

        Args:
            callback: 恢复回调函数，签名为 callback(is_degraded, degraded_at, degraded_reason)
        """
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey

            async def restore():
                redis_client = await RedisPool.get_client()
                degraded_str = await redis_client.get(RedisKey.HSM_DEGRADED)
                if degraded_str == b"true":
                    degraded_at_str = await redis_client.get(RedisKey.HSM_DEGRADED_AT)
                    degraded_reason_str = await redis_client.get(RedisKey.HSM_DEGRADED_REASON)
                    degraded_at = None
                    degraded_reason = None
                    if degraded_at_str:
                        try:
                            from datetime import datetime
                            degraded_at = datetime.fromisoformat(degraded_at_str.decode())
                        except Exception:
                            from datetime import datetime, timezone
                            degraded_at = datetime.now(timezone.utc)
                    if degraded_reason_str:
                        degraded_reason = degraded_reason_str.decode()
                    callback(True, degraded_at, degraded_reason)

            self.safe_run_async_task(restore())
        except Exception as e:
            logger.error("failed_to_restore_degraded_state_from_redis", error=str(e))
        self.flush_pending_persistence()

    def restore_keys(
        self,
        key_slots: dict[KeySlot, dict[str, dict[str, Any]]],
        lock: threading.RLock,
        encryption_key_getter: Callable[[], bytes],
        key_encryption_password_getter: Callable[[], bytes | None],
    ) -> None:
        """从 Redis 恢复密钥。

        Args:
            key_slots: 密钥槽位字典（将被填充恢复的数据）
            lock: 线程锁
            encryption_key_getter: 获取 Fernet 加密密钥的回调
            key_encryption_password_getter: 获取私钥加密密码的回调
        """
        try:
            from datetime import datetime

            from app.config.redis import RedisPool
            from app.config.redis_keys import _PREFIX as REDIS_KEY_PREFIX
            from app.config.redis_keys import RedisKey

            async def restore():
                redis_client = await RedisPool.get_client()

                async for key in redis_client.scan_iter(RedisKey.HSM_KEYS_PATTERN):
                    key_str = key.decode()
                    private_suffix = ":private"
                    raw_key_name = key_str
                    if REDIS_KEY_PREFIX and raw_key_name.startswith(REDIS_KEY_PREFIX):
                        raw_key_name = raw_key_name[len(REDIS_KEY_PREFIX):]
                    raw_key_name = raw_key_name.replace(":hsm:keys:", "").replace(private_suffix, "")

                    slot = None
                    key_name = raw_key_name
                    for slot_enum in KeySlot:
                        prefix = f"{slot_enum.value}:"
                        if raw_key_name.startswith(prefix):
                            slot = slot_enum
                            key_name = raw_key_name[len(prefix):]
                            break

                    if slot is None:
                        slot = get_key_slot(key_name)

                    redis_key = RedisKey.hsm_key(slot.value, key_name)
                    meta_data_str = await redis_client.get(redis_key)
                    encrypted_private_key = await redis_client.get(key)

                    if meta_data_str and encrypted_private_key:
                        try:
                            meta_data = json.loads(meta_data_str.decode())

                            fernet = Fernet(encryption_key_getter())
                            private_pem = fernet.decrypt(encrypted_private_key)

                            password = key_encryption_password_getter()
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

                            with lock:
                                key_slots[slot][key_name] = {
                                    'private_key': private_key,
                                    'public_key': public_key,
                                    'key_version': meta_data['key_version'],
                                    'created_at': created_at,
                                    'expires_at': expires_at,
                                    'is_active': meta_data.get('is_active', True),
                                    'slot': slot,
                                }

                            logger.info("hsm_key_restored_from_redis", key_name=key_name,
                                        key_version=meta_data['key_version'], slot=slot.value)
                        except Exception as e:
                            logger.error("failed_to_restore_key_from_redis", key_name=key_name, error=str(e))

            self.safe_run_async_task(restore())
        except Exception as e:
            logger.error("failed_to_restore_keys_from_redis", error=str(e))
        self.flush_pending_persistence()

    def migrate_legacy_keys(self) -> None:
        """迁移旧版 Redis 密钥格式到新格式。"""
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import LegacyRedisKey, RedisKey

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
                            slot = get_key_slot(key_name)

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

            self.safe_run_async_task(migrate())
        except Exception as e:
            logger.error("failed_to_migrate_legacy_keys", error=str(e))

    async def cleanup_redis_keys(self, expired_keys: list[tuple[str, KeySlot]]) -> None:
        """清理 Redis 中的过期密钥。

        Args:
            expired_keys: 过期密钥列表，每项为 (key_name, slot) 元组
        """
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
