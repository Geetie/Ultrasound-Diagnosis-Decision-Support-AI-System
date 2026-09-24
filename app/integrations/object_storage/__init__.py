"""T-GB-SEC-02 — 对象存储冷归档适配器。

契约：:class:`ObjectStorageAdapter`（Protocol）。实现：

- :class:`LocalFakeObjectStorage` — 本地假实现：``root_dir`` 给定时写盘
  （跨进程可恢复），为 ``None`` 时用进程内内存（单元测试用）；
- :class:`S3ObjectStorage` — MinIO / S3 实现（boto3），带重试与指数退避。

由 :func:`create_object_storage` 按 ``settings.object_storage_adapter`` 选择；
现场亦可用 :func:`set_object_storage` 注入自定义实现。

冷层不可用时**不得丢数据**：冷归档服务在对象上传成功前不会删除热数据，
失败只记 ``failed`` 状态并在下一轮重试（见 ``app/services/cold_archive.py``）。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import structlog

from app.config.settings import settings

logger = structlog.get_logger("app.integrations.object_storage")


@dataclass
class StoredObject:
    """一次上传的结果。"""

    key: str
    size_bytes: int
    checksum: str
    adapter: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "adapter": self.adapter,
        }


@runtime_checkable
class ObjectStorageAdapter(Protocol):
    """冷层对象存储契约。"""

    @property
    def name(self) -> str:
        ...

    async def put(self, key: str, payload: bytes) -> StoredObject:
        """写入对象（覆盖同名对象）。"""
        ...

    async def get(self, key: str) -> Optional[bytes]:
        """读取对象；不存在返回 ``None``。"""
        ...

    async def exists(self, key: str) -> bool:
        ...

    async def delete(self, key: str) -> bool:
        ...

    async def list_keys(self, prefix: str = "") -> List[str]:
        ...

    async def health(self) -> bool:
        ...


def _checksum(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class LocalFakeObjectStorage:
    """本地假实现。

    Args:
        root_dir: 落盘目录；``None`` 时使用进程内内存字典。
            传目录可让"归档 → 重启 → 恢复"在本地闭环验证。
    """

    def __init__(self, root_dir: Optional[str] = None) -> None:
        self._root = Path(root_dir) if root_dir else None
        self._memory: Dict[str, bytes] = {}
        if self._root is not None:
            self._root.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "local_fake"

    def _path(self, key: str) -> Path:
        # 归一化 key，防止 ../ 逃逸出 root_dir
        safe = key.replace("\\", "/").lstrip("/")
        safe = "/".join(part for part in safe.split("/") if part not in ("", ".", ".."))
        assert self._root is not None
        return self._root / safe

    async def put(self, key: str, payload: bytes) -> StoredObject:
        if self._root is None:
            self._memory[key] = payload
        else:
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_bytes, payload)
        logger.info("object_storage_put", key=key, size=len(payload))
        return StoredObject(
            key=key,
            size_bytes=len(payload),
            checksum=_checksum(payload),
            adapter=self.name,
        )

    async def get(self, key: str) -> Optional[bytes]:
        if self._root is None:
            return self._memory.get(key)
        path = self._path(key)
        if not await asyncio.to_thread(path.exists):
            return None
        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, key: str) -> bool:
        if self._root is None:
            return key in self._memory
        return await asyncio.to_thread(self._path(key).exists)

    async def delete(self, key: str) -> bool:
        if self._root is None:
            return self._memory.pop(key, None) is not None
        path = self._path(key)
        if not await asyncio.to_thread(path.exists):
            return False
        await asyncio.to_thread(path.unlink)
        return True

    async def list_keys(self, prefix: str = "") -> List[str]:
        if self._root is None:
            return sorted(k for k in self._memory if k.startswith(prefix))
        base = self._root
        if not await asyncio.to_thread(base.exists):
            return []
        keys: List[str] = []
        for path in base.rglob("*"):
            if await asyncio.to_thread(path.is_file):
                rel = str(path.relative_to(base)).replace(os.sep, "/")
                if rel.startswith(prefix):
                    keys.append(rel)
        return sorted(keys)

    async def health(self) -> bool:
        return True


class MinioObjectStorage:
    """MinIO / S3 兼容实现（复用项目已声明的 ``minio`` 依赖），带重试与退避。

    与 :class:`app.services.dicom_storage` 的 MinIO 客户端构造方式保持一致
    （同一个 ``minio_endpoint`` / 凭证配置口径）。
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str = "",
        access_key: str = "",
        secret_key: str = "",
        secure: bool = False,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
        create_bucket: bool = True,
    ) -> None:
        if not bucket:
            raise ValueError("MinioObjectStorage requires a non-empty bucket")
        if not endpoint:
            raise ValueError("MinioObjectStorage requires a non-empty endpoint")
        self._bucket = bucket
        self._endpoint = endpoint
        self._access_key = access_key
        self._secret_key = secret_key
        self._secure = secure
        self._max_retries = max(1, max_retries)
        self._backoff = backoff_seconds
        self._create_bucket = create_bucket
        self._client_instance: Any = None

    @property
    def name(self) -> str:
        return "s3"

    def _client(self):
        if self._client_instance is None:
            from minio import Minio

            self._client_instance = Minio(
                self._endpoint,
                access_key=self._access_key,
                secret_key=self._secret_key,
                secure=self._secure,
            )
            if self._create_bucket and not self._client_instance.bucket_exists(
                self._bucket
            ):
                self._client_instance.make_bucket(self._bucket)
        return self._client_instance

    async def _with_retry(self, operation: str, func, *args, **kwargs):
        attempt = 0
        last_error: Optional[Exception] = None
        while attempt < self._max_retries:
            attempt += 1
            try:
                return await asyncio.to_thread(func, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 网络/权限异常一律重试
                last_error = exc
                logger.warning(
                    "object_storage_attempt_failed",
                    operation=operation,
                    attempt=attempt,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff * (2 ** (attempt - 1)))
        raise RuntimeError(
            f"object storage {operation} failed after {attempt} attempts: {last_error}"
        )

    async def put(self, key: str, payload: bytes) -> StoredObject:
        def _put():
            from io import BytesIO

            client = self._client()
            client.put_object(
                self._bucket, key, BytesIO(payload), length=len(payload)
            )
            return True

        await self._with_retry("put", _put)
        logger.info("object_storage_put", key=key, size=len(payload), adapter=self.name)
        return StoredObject(
            key=key,
            size_bytes=len(payload),
            checksum=_checksum(payload),
            adapter=self.name,
        )

    async def get(self, key: str) -> Optional[bytes]:
        def _get():
            client = self._client()
            response = None
            try:
                response = client.get_object(self._bucket, key)
                return response.read()
            except Exception as exc:  # noqa: BLE001 - 缺对象按 None 处理
                name = type(exc).__name__
                if name in ("S3Error", "NoSuchKey") or "does not exist" in str(exc):
                    return None
                raise
            finally:
                if response is not None:
                    response.close()
                    response.release_conn()

        return await self._with_retry("get", _get)

    async def exists(self, key: str) -> bool:
        def _stat():
            try:
                self._client().stat_object(self._bucket, key)
                return True
            except Exception:  # noqa: BLE001 - 不存在即 False
                return False

        return bool(await self._with_retry("stat", _stat))

    async def delete(self, key: str) -> bool:
        def _delete():
            self._client().remove_object(self._bucket, key)
            return True

        await self._with_retry("delete", _delete)
        return True

    async def list_keys(self, prefix: str = "") -> List[str]:
        def _list():
            client = self._client()
            return [
                obj.object_name
                for obj in client.list_objects(
                    self._bucket, prefix=prefix, recursive=True
                )
            ]

        return await self._with_retry("list", _list)

    async def health(self) -> bool:
        def _health():
            return bool(self._client().bucket_exists(self._bucket))

        try:
            return bool(await self._with_retry("bucket_exists", _health))
        except Exception:  # noqa: BLE001
            return False


_adapter: Optional[ObjectStorageAdapter] = None


def set_object_storage(adapter: Optional[ObjectStorageAdapter]) -> None:
    """注入（或清除）进程级对象存储适配器。"""
    global _adapter
    _adapter = adapter


def create_object_storage(settings_obj: Any = None) -> ObjectStorageAdapter:
    """按 ``settings.object_storage_adapter`` 构造适配器。

    Raises:
        ValueError: 名字未知。显式失败而非静默降级，避免"看起来归档了"。
    """
    cfg = settings_obj or settings
    name = str(getattr(cfg, "object_storage_adapter", "local_fake"))
    if name == "local_fake":
        root = str(getattr(cfg, "object_storage_local_dir", "") or "") or None
        return LocalFakeObjectStorage(root_dir=root)
    if name in ("s3", "minio"):
        return MinioObjectStorage(
            bucket=str(getattr(cfg, "object_storage_bucket", "") or ""),
            endpoint=str(getattr(cfg, "object_storage_endpoint", "") or ""),
            access_key=str(getattr(cfg, "object_storage_access_key", "") or ""),
            secret_key=str(getattr(cfg, "object_storage_secret_key", "") or ""),
            secure=bool(getattr(cfg, "minio_secure", False)),
            max_retries=int(getattr(cfg, "object_storage_max_retries", 3)),
            backoff_seconds=float(
                getattr(cfg, "object_storage_retry_backoff_seconds", 0.5)
            ),
        )
    raise ValueError(
        f"unknown object_storage_adapter: {name!r} "
        "(expected 'local_fake', 's3' or 'minio'; use set_object_storage() to inject)"
    )


def get_object_storage() -> ObjectStorageAdapter:
    """取得进程级适配器（已注入的优先，否则按配置构造）。"""
    global _adapter
    if _adapter is None:
        _adapter = create_object_storage()
    return _adapter


def build_object_key(prefix: str, category: str, manifest_id: str) -> str:
    """冷层对象键约定：``{prefix}/{category}/{manifest_id}.jsonl``。"""
    clean = "/".join(p for p in (prefix or "").split("/") if p)
    return f"{clean}/{category}/{manifest_id}.jsonl" if clean else f"{category}/{manifest_id}.jsonl"


__all__ = [
    "LocalFakeObjectStorage",
    "MinioObjectStorage",
    "ObjectStorageAdapter",
    "StoredObject",
    "build_object_key",
    "create_object_storage",
    "get_object_storage",
    "set_object_storage",
]
