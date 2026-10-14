import asyncio
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config.redis_keys import RedisKey
from app.security.hsm import KeySlot, hsm_manager

logger = structlog.get_logger()

_DESENSITIZATION_KEY_NAME = "desens_mapping_key"
_AES_KEY_WRAP_NAME = "desens_aes_wrapped"
_KEY_CACHE_TTL = timedelta(minutes=5)
_AES_KEY_SIZE = 32


class ReEncryptionError(Exception):
    def __init__(self, message: str, processed_count: int, resume_from_id: str):
        super().__init__(message)
        self.processed_count = processed_count
        self.resume_from_id = resume_from_id


@dataclass
class DesensitizationMappingRecord:
    record_id: str
    session_id: str
    original_value: str
    desensitized_value: str
    field_type: str
    key_version: int
    created_at: datetime


class DesensitizationRestoreService:
    def __init__(self) -> None:
        self._key_cache: bytes | None = None
        self._key_cache_at: datetime | None = None
        self._current_key_version: int = 0
        self._lock = threading.Lock()
        self._async_lock = asyncio.Lock()
        self._rate_limit_counter: dict[str, int] = {}
        self._rate_limit_window_start: datetime | None = None

    def _ensure_hsm_key(self) -> None:
        key_info = hsm_manager.get_key_info(_DESENSITIZATION_KEY_NAME)
        if not key_info:
            hsm_manager.generate_key_pair(
                _DESENSITIZATION_KEY_NAME, slot=KeySlot.DESENSITIZATION_MAPPING
            )

    async def _get_or_create_aes_key(self) -> tuple:
        self._ensure_hsm_key()

        key_info = hsm_manager.get_key_info(_DESENSITIZATION_KEY_NAME)
        key_version = key_info['key_version'] if key_info else 1

        try:
            from app.config.redis import RedisPool

            redis_client = await RedisPool.get_client()
            wrapped_data = await redis_client.get(RedisKey.hsm_desens_aes_key(key_version))
            stored_version = await redis_client.get(RedisKey.HSM_DESENS_AES_KEY_VERSION)

            if wrapped_data and stored_version:
                try:
                    stored_ver = int(stored_version.decode() if isinstance(stored_version, bytes) else stored_version)
                    if stored_ver == key_version:
                        plaintext_key = hsm_manager.decrypt(_DESENSITIZATION_KEY_NAME, wrapped_data)
                        return plaintext_key, key_version
                except Exception as e:
                    logger.warning("failed_to_unwrap_aes_key_from_redis", error=str(e))
        except Exception as e:
            logger.warning("failed_to_load_aes_key_from_redis", error=str(e))

        aes_key = os.urandom(_AES_KEY_SIZE)

        try:
            from app.config.redis import RedisPool

            redis_client = await RedisPool.get_client()
            wrapped = hsm_manager.encrypt(_DESENSITIZATION_KEY_NAME, aes_key)
            ttl_seconds = 180 * 86400
            async with redis_client.pipeline(transaction=True) as pipe:
                await pipe.set(RedisKey.hsm_desens_aes_key(key_version), wrapped, ex=ttl_seconds)
                await pipe.set(RedisKey.HSM_DESENS_AES_KEY_VERSION, str(key_version), ex=ttl_seconds)
                await pipe.execute()
        except Exception as e:
            logger.critical("failed_to_store_wrapped_aes_key", error=str(e),
                           key_version=key_version)
            if not (os.environ.get("TEST_MODE") or os.environ.get("PYTEST_CURRENT_TEST")):
                raise RuntimeError(
                    f"Cannot persist AES key (version={key_version}) to Redis. "
                    f"Encrypting data without a persisted key would cause irreversible data loss. "
                    f"Original error: {e}"
                )

        return aes_key, key_version

    async def _get_aes_key(self) -> tuple:
        now = datetime.now(timezone.utc)
        with self._lock:
            if self._key_cache is not None and self._key_cache_at is not None:
                if now - self._key_cache_at < _KEY_CACHE_TTL:
                    return self._key_cache, self._current_key_version

        aes_key, key_version = await self._get_or_create_aes_key()

        with self._lock:
            if self._key_cache is None or self._key_cache_at is None or \
               (now - self._key_cache_at) >= _KEY_CACHE_TTL:
                self._key_cache = aes_key
                self._key_cache_at = now
                self._current_key_version = key_version
            else:
                aes_key = self._key_cache
                key_version = self._current_key_version

        return aes_key, key_version

    async def _encrypt_value(self, plaintext: str) -> tuple:
        aes_key, key_version = await self._get_aes_key()
        iv = os.urandom(12)
        aesgcm = AESGCM(aes_key)
        ciphertext = aesgcm.encrypt(iv, plaintext.encode('utf-8'), None)
        return ciphertext, iv, key_version

    async def _decrypt_value(self, ciphertext: bytes, iv: bytes, key_version: int) -> str:
        aes_key, current_version = await self._get_aes_key()
        if key_version != current_version:
            logger.warning("decrypting_with_different_key_version",
                          stored_version=key_version, current_version=current_version)
            try:
                from app.config.redis import RedisPool

                redis_client = await RedisPool.get_client()
                wrapped_old = await redis_client.get(RedisKey.hsm_desens_aes_key(key_version))
                if wrapped_old:
                    old_key = hsm_manager.decrypt(_DESENSITIZATION_KEY_NAME, wrapped_old)
                    aesgcm = AESGCM(old_key)
                    plaintext = aesgcm.decrypt(iv, ciphertext, None)
                    return plaintext.decode('utf-8')
            except Exception as e:
                logger.error("failed_to_decrypt_with_old_key_version",
                            stored_version=key_version, error=str(e))
                raise RuntimeError(
                    f"Cannot decrypt data with key_version={key_version}. "
                    f"Current key_version={current_version}. "
                    f"Old key unavailable or decryption failed."
                ) from e
            # A.62 批 2-D 修正：走到这里 = 「版本不同 **而且** 旧密钥记录不存在」。
            # 原先会**掉进下面的"用当前密钥解密"分支**，最后抛的是密码学库的 InvalidTag ——
            # 错误类型与契约不符（调用方按 RuntimeError 处理），而且异常信息毫无指引。
            # 现在显式抛约定异常，语义与"旧密钥不可用"一致（graceful 路径仍然保留：
            # 旧密钥「存在且能解」时上面已 return）。
            raise RuntimeError(
                f"Cannot decrypt data with key_version={key_version}. "
                f"Current key_version={current_version}. "
                f"Old key unavailable or decryption failed."
            )

        aesgcm = AESGCM(aes_key)
        plaintext = aesgcm.decrypt(iv, ciphertext, None)
        return plaintext.decode('utf-8')

    async def _check_rate_limit(self) -> bool:
        from app.config.settings import settings
        limit = settings.desensitization_restore_rate_limit
        now = datetime.now(timezone.utc)

        async with self._async_lock:
            if self._rate_limit_window_start is None or \
               (now - self._rate_limit_window_start) > timedelta(hours=1):
                self._rate_limit_counter = {}
                self._rate_limit_window_start = now

            hour_key = now.strftime("%Y%m%d%H")
            current_count = self._rate_limit_counter.get(hour_key, 0)
            if current_count >= limit:
                return False
            self._rate_limit_counter[hour_key] = current_count + 1

        return True

    async def _check_rate_limit_persistent(self) -> bool:
        from app.config.settings import settings
        limit = settings.desensitization_restore_rate_limit
        now = datetime.now(timezone.utc)
        hour_key = now.strftime("%Y%m%d%H")
        redis_key = RedisKey.desens_restore_rate(hour_key)

        if not await self._check_rate_limit():
            return False

        try:
            from app.config.redis import RedisPool
            redis_client = await RedisPool.get_client()
            incr_expire_script = """
            local count = redis.call('INCR', KEYS[1])
            if count == 1 then
                redis.call('EXPIRE', KEYS[1], ARGV[1])
            end
            return count
            """
            count = await redis_client.eval(incr_expire_script, 1, redis_key, 7200)
            if count > limit:
                async with self._async_lock:
                    self._rate_limit_counter[hour_key] = max(
                        0, self._rate_limit_counter.get(hour_key, 1) - 1
                    )
                await self._handle_rate_limit_exceeded(
                    count=count, limit=limit, hour_key=hour_key
                )
                return False
        except Exception as e:
            logger.warning("failed_to_check_persistent_rate_limit", error=str(e))

        return True

    async def _handle_rate_limit_exceeded(
        self, *, count: int, limit: int, hour_key: str
    ) -> None:
        """超限统一处理：日志 + 告警（审计 + 通知数据安全官）。

        单独抽出的原因有二：一是超限提示与告警链路只有一处实现；
        二是 conftest 会把 ``_check_rate_limit_persistent`` 整体打桩
        （测试期不希望限流生效），抽出的本方法仍可被单测直接覆盖。
        """
        logger.warning(
            "desensitization_restore_rate_limit_exceeded_persistent",
            count=count,
            limit=limit,
        )
        await self._raise_rate_limit_alert(count=count, limit=limit, hour_key=hour_key)

    async def _raise_rate_limit_alert(
        self, *, count: int, limit: int, hour_key: str
    ) -> None:
        """触发超限告警（审计 + 通知数据安全官）。

        告警链路是旁路：任何异常都不得影响"拒绝还原"这一安全动作本身。
        """
        try:
            from app.security.restore_rate_monitor import get_restore_rate_monitor

            await get_restore_rate_monitor().evaluate(
                count=count,
                limit=limit,
                hour_key=hour_key,
            )
        except Exception as e:
            logger.error("restore_rate_alert_dispatch_failed", error=str(e))

    async def store_mapping(
        self,
        session_id: str,
        field_type: str,
        original_value: str,
        desensitized_value: str,
    ) -> str:
        record_id = str(uuid.uuid4())
        encrypted_original, iv, key_version = await self._encrypt_value(original_value)

        try:
            from app.config.database import get_session
            from app.models.desensitization_mapping import DesensitizationMapping

            async for db_session in get_session():
                mapping = DesensitizationMapping(
                    id=record_id,
                    session_id=session_id,
                    encrypted_original=encrypted_original,
                    iv=iv,
                    desensitized_value=desensitized_value,
                    field_type=field_type,
                    key_version=key_version,
                )
                db_session.add(mapping)
                await db_session.commit()

        except Exception as e:
            logger.error("failed_to_store_desensitization_mapping",
                        record_id=record_id, error=str(e))
            raise

        try:
            from app.services.audit_service import AuditService
            audit_svc = AuditService()
            await audit_svc.log_action(
                action="DESENSITIZATION_MAPPING_STORED",
                actor="system",
                target_type="desensitization_mapping",
                target_id=record_id,
                detail={
                    "session_id": session_id,
                    "field_type": field_type,
                    "key_version": key_version,
                },
            )
        except Exception as e:
            logger.warning("failed_to_audit_mapping_storage", error=str(e))

        logger.info("desensitization_mapping_stored",
                    record_id=record_id, session_id=session_id,
                    field_type=field_type, key_version=key_version)
        return record_id

    async def restore_value(self, record_id: str) -> str:
        if not await self._check_rate_limit_persistent():
            raise RuntimeError("Desensitization restore rate limit exceeded")

        try:
            from sqlalchemy import select

            from app.config.database import get_session
            from app.models.desensitization_mapping import DesensitizationMapping

            async for db_session in get_session():
                result = await db_session.execute(
                    select(DesensitizationMapping).where(DesensitizationMapping.id == record_id)
                )
                mapping = result.scalar_one_or_none()
                if not mapping:
                    raise ValueError(f"Mapping record {record_id} not found")

                original_value = await self._decrypt_value(
                    mapping.encrypted_original,
                    mapping.iv,
                    mapping.key_version,
                )

        except ValueError:
            raise
        except Exception as e:
            logger.error("failed_to_restore_desensitization_value",
                        record_id=record_id, error=str(e))
            raise

        try:
            from app.services.audit_service import AuditService
            audit_svc = AuditService()
            await audit_svc.log_action(
                action="DESENSITIZATION_VALUE_RESTORED",
                actor="system",
                target_type="desensitization_mapping",
                target_id=record_id,
                detail={"key_version": mapping.key_version},
            )
        except Exception as e:
            logger.warning("failed_to_audit_mapping_restore", error=str(e))

        return original_value

    async def batch_restore(self, record_ids: list[str]) -> dict[str, str | None]:
        if not record_ids:
            logger.debug("batch_restore_empty_request")
            return {}

        if not await self._check_rate_limit_persistent():
            logger.warning("batch_restore_rate_limited", record_count=len(record_ids))
            raise RuntimeError("Desensitization restore rate limit exceeded")

        logger.info("batch_restore_started", record_count=len(record_ids), record_ids_sample=record_ids[:5])
        start_time = time.monotonic()

        from sqlalchemy import select

        from app.config.database import get_session
        from app.models.desensitization_mapping import DesensitizationMapping

        mappings_by_id: dict[str, DesensitizationMapping] = {}
        async for db_session in get_session():
            result = await db_session.execute(
                select(DesensitizationMapping).where(DesensitizationMapping.id.in_(record_ids))
            )
            for mapping in result.scalars().all():
                mappings_by_id[mapping.id] = mapping
            break

        semaphore = asyncio.Semaphore(5)

        async def _restore_one(record_id: str) -> tuple[str, str | None]:
            mapping = mappings_by_id.get(record_id)
            if not mapping:
                return record_id, None
            async with semaphore:
                try:
                    value = await self._decrypt_value(
                        mapping.encrypted_original, mapping.iv, mapping.key_version,
                    )
                    return record_id, value
                except Exception as e:
                    logger.error("batch_restore_failed_for_record",
                                record_id=record_id, error=str(e))
                    return record_id, None

        tasks = [_restore_one(rid) for rid in record_ids]
        results_list = await asyncio.gather(*tasks)

        success_count = sum(1 for _, v in results_list if v is not None)
        not_found_count = sum(1 for rid, _ in results_list if rid not in mappings_by_id)
        failed_count = len(results_list) - success_count - not_found_count
        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info("batch_restore_completed",
                    total=len(results_list),
                    success=success_count,
                    not_found=not_found_count,
                    failed=failed_count,
                    elapsed_ms=round(elapsed_ms, 2))

        return dict(results_list)

    async def re_encrypt_with_new_key(self, new_key_version: int, resume_from_id: str | None = None) -> int:
        try:
            from sqlalchemy import select

            from app.config.database import get_session
            from app.models.desensitization_mapping import DesensitizationMapping

            total_re_encrypted = 0
            failed_count = 0
            batch_size = 1000
            last_processed_id = resume_from_id

            async for db_session in get_session():
                query = select(DesensitizationMapping).where(
                    DesensitizationMapping.key_version < new_key_version
                )
                if last_processed_id:
                    query = query.where(DesensitizationMapping.id > last_processed_id)
                query = query.order_by(DesensitizationMapping.id).limit(batch_size)

                result = await db_session.execute(query)
                mappings = result.scalars().all()

                while mappings:
                    for mapping in mappings:
                        try:
                            original_value = await self._decrypt_value(
                                mapping.encrypted_original,
                                mapping.iv,
                                mapping.key_version,
                            )
                            new_encrypted, new_iv, actual_version = await self._encrypt_value(original_value)

                            mapping.encrypted_original = new_encrypted
                            mapping.iv = new_iv
                            mapping.key_version = actual_version
                            total_re_encrypted += 1
                            last_processed_id = mapping.id
                        except Exception as e:
                            logger.error("re_encrypt_failed_for_record",
                                        record_id=mapping.id, error=str(e))
                            failed_count += 1

                    await db_session.commit()

                    if total_re_encrypted > 0 and failed_count > total_re_encrypted * 0.1:
                        raise ReEncryptionError(
                            f"Too many failures: {failed_count}/{total_re_encrypted}",
                            processed_count=total_re_encrypted,
                            resume_from_id=last_processed_id or "",
                        )

                    query = select(DesensitizationMapping).where(
                        DesensitizationMapping.key_version < new_key_version
                    )
                    if last_processed_id:
                        query = query.where(DesensitizationMapping.id > last_processed_id)
                    query = query.order_by(DesensitizationMapping.id).limit(batch_size)

                    result = await db_session.execute(query)
                    mappings = result.scalars().all()

            logger.info("desensitization_re_encrypt_completed",
                       total_re_encrypted=total_re_encrypted,
                       failed_count=failed_count,
                       new_key_version=new_key_version)
            return total_re_encrypted

        except ReEncryptionError:
            raise
        except Exception as e:
            logger.error("failed_to_re_encrypt_mappings", error=str(e))
            raise ReEncryptionError(str(e), 0, resume_from_id or "")

    def get_restore_stats(self) -> dict[str, Any]:
        with self._lock:
            total_requests = sum(self._rate_limit_counter.values()) if self._rate_limit_counter else 0
        return {
            "total_restore_requests_current_window": total_requests,
            "current_key_version": self._current_key_version,
            "key_cache_valid": self._key_cache is not None and self._key_cache_at is not None,
        }


_desensitization_restore_instance: DesensitizationRestoreService | None = None
_desensitization_restore_lock = threading.Lock()


def get_desensitization_restore_service() -> DesensitizationRestoreService:
    global _desensitization_restore_instance
    if _desensitization_restore_instance is None:
        with _desensitization_restore_lock:
            if _desensitization_restore_instance is None:
                _desensitization_restore_instance = DesensitizationRestoreService()
    return _desensitization_restore_instance
