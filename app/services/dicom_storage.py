"""DICOM文件存储服务 - 支持本地文件系统和MinIO后端"""
import asyncio
import os
import re
import shutil
from datetime import datetime, timezone
from io import BytesIO

import aiofiles
import structlog

from app.config.settings import settings

logger = structlog.get_logger()

_PHI_TAGS = [
    (0x0010, 0x0010),
    (0x0010, 0x0030),
    (0x0010, 0x1000),
    (0x0010, 0x1001),
    (0x0010, 0x1040),
    (0x0010, 0x2154),
    (0x0008, 0x0050),
    (0x0008, 0x0080),
    (0x0008, 0x0081),
    (0x0008, 0x0090),
    (0x0008, 0x1070),
    (0x0010, 0x1020),
    (0x0010, 0x1021),
    (0x0010, 0x1030),
    (0x0010, 0x2155),
    (0x0010, 0x2160),
    (0x0010, 0x2180),
    (0x0010, 0x21B0),
    (0x0010, 0x21D0),
    (0x0010, 0x21F0),
    (0x0010, 0x2201),
    (0x0010, 0x2202),
    (0x0010, 0x2203),
    (0x0008, 0x009C),
    (0x0008, 0x009D),
    (0x0008, 0x1060),
    (0x0008, 0x1072),
    (0x0008, 0x1080),
    (0x0008, 0x1084),
    (0x0008, 0x1110),
    (0x0008, 0x1120),
]


def strip_dicom_phi(content: bytes, patient_id: str) -> tuple[bytes, bool]:
    """DICOM PHI 去标识化。失败即抛错（fail-closed）——调用方拒绝存储。

    P0-5（15 号计划）：原实现 except 分支 `return content, False` 会把
    **未脱敏原文**继续落盘（fail-open），患者 PHI 直接入库，真机审计判定
    为红线缺陷。
    """
    stripped = False
    try:
        import pydicom

        ds = pydicom.dcmread(BytesIO(content), force=True)

        for group, elem in _PHI_TAGS:
            if (group, elem) in ds:
                del ds[(group, elem)]
                stripped = True

        ds.remove_private_tags()
        stripped = True

        if (0x0010, 0x0020) in ds:
            ds[0x0010, 0x0020].value = patient_id
            stripped = True

        buf = BytesIO()
        ds.save_as(buf)
        return buf.getvalue(), stripped
    except Exception as e:
        logger.error("dicom_phi_strip_failed_fail_closed", error=str(e))
        raise ValueError(
            f"DICOM PHI 去标识化失败，拒绝存储（fail-closed）: {e}"
        ) from e


class DicomStorageService:

    def __init__(self):
        self._backend = settings.dicom_storage_backend
        self._base_path = settings.dicom_storage_path
        self._max_file_size = settings.dicom_storage_max_file_size
        self._minio_client = None
        if self._backend == "local":
            os.makedirs(self._base_path, exist_ok=True)

    def _get_minio_client(self):
        if self._minio_client is None:
            from minio import Minio
            self._minio_client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
            if not self._minio_client.bucket_exists(settings.minio_bucket):
                self._minio_client.make_bucket(settings.minio_bucket)
        return self._minio_client

    def _build_storage_path(self, patient_id: str, session_id: str, sop_instance_uid: str) -> str:
        date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
        return os.path.join(
            self._base_path,
            date_prefix,
            patient_id,
            session_id,
            f"{sop_instance_uid}.dcm",
        )

    # ------------------------------------------------------------------
    # 按存储 ID 存取（供 WADO 回读用）
    #
    # 背景：上传/推流接口只把 `img_YYYYMMDD_hex8` / `frame_YYYYMMDD_hex8`
    # 这类 ID 返回给客户端，而 `_build_storage_path` 生成的是
    # `{date}/{patient}/{session}/{sop}.dcm` —— 两者之间**没有任何映射记录**，
    # 所以此前无法按 ID 取回字节（浏览器显示 DICOM 缺的就是这条回读路径）。
    #
    # 这里不引入数据库表：ID 本身就带日期（`img_20261002_ab12cd34` 中的
    # `20261002`），因此路径可由 ID **确定性推导**，无需索引、也不会把
    # 文件系统路径暴露给客户端。
    # ------------------------------------------------------------------

    STORAGE_ID_PATTERN = re.compile(r"^(?:img|frame)_(\d{8})_[0-9a-f]{8}$")

    def path_for_id(self, storage_id: str) -> str | None:
        """由存储 ID 反推绝对路径；ID 格式非法或不含日期时返回 None。"""
        m = self.STORAGE_ID_PATTERN.match(storage_id or "")
        if not m:
            return None
        date_prefix = m.group(1)
        if self._backend == "minio":
            return f"minio://{settings.minio_bucket}/{date_prefix}/{storage_id}.dcm"
        candidate = os.path.join(self._base_path, date_prefix, f"{storage_id}.dcm")
        # 双保险：即使 pattern 将来放宽，也必须仍落在 base 之内
        base = os.path.abspath(self._base_path)
        if os.path.abspath(candidate).startswith(base + os.sep):
            return candidate
        return None

    async def save_by_id(self, content: bytes, storage_id: str, patient_id: str) -> str:
        """按存储 ID 落盘（去 PHI 后）。返回可交给 retrieve() 的路径。"""
        if len(content) > self._max_file_size:
            raise ValueError(
                f"DICOM file size {len(content)} exceeds maximum {self._max_file_size}"
            )

        storage_path = self.path_for_id(storage_id)
        if storage_path is None:
            raise ValueError(f"Invalid storage id: {storage_id}")

        # 并发审查 P0-3：strip_dicom_phi 内部 dcmread + remove_private_tags +
        # save_as（同一份数据解析两遍、序列化一遍），超声单帧 1~30MB 时
        # 典型 20~150ms 纯 CPU——必须 offload，否则并发上传会把事件循环打满。
        stripped_content, was_stripped = await asyncio.to_thread(
            strip_dicom_phi, content, patient_id
        )
        if was_stripped:
            logger.info("dicom_phi_stripped_before_save", storage_id=storage_id)

        if self._backend == "local":
            os.makedirs(os.path.dirname(storage_path), exist_ok=True)
            temp_path = storage_path + ".tmp"
            try:
                async with aiofiles.open(temp_path, "wb") as f:
                    await f.write(stripped_content)
                shutil.move(temp_path, storage_path)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
            logger.info(
                "dicom_saved_by_id",
                backend="local",
                storage_id=storage_id,
                size=len(stripped_content),
            )
            return storage_path

        if self._backend == "minio":
            client = self._get_minio_client()
            bucket, _, key = storage_path.partition("minio://")[2].partition("/")
            client.put_object(bucket, key, stripped_content, content_type="application/dicom")
            return storage_path

        raise ValueError(f"Unknown storage backend: {self._backend}")

    async def save(
        self,
        content: bytes,
        patient_id: str,
        session_id: str,
        sop_instance_uid: str,
    ) -> str:
        if len(content) > self._max_file_size:
            raise ValueError(
                f"DICOM file size {len(content)} exceeds maximum {self._max_file_size}"
            )

        stripped_content, was_stripped = await asyncio.to_thread(
            strip_dicom_phi, content, patient_id
        )
        if was_stripped:
            logger.info("dicom_phi_stripped_before_save", patient_id=patient_id, session_id=session_id)

        if self._backend == "local":
            return await self._save_local(stripped_content, patient_id, session_id, sop_instance_uid)
        elif self._backend == "minio":
            return await self._save_minio(stripped_content, patient_id, session_id, sop_instance_uid)
        else:
            raise ValueError(f"Unknown storage backend: {self._backend}")

    async def _save_local(
        self,
        content: bytes,
        patient_id: str,
        session_id: str,
        sop_instance_uid: str,
    ) -> str:
        storage_path = self._build_storage_path(patient_id, session_id, sop_instance_uid)
        os.makedirs(os.path.dirname(storage_path), exist_ok=True)

        temp_path = storage_path + ".tmp"
        try:
            async with aiofiles.open(temp_path, "wb") as f:
                await f.write(content)
            shutil.move(temp_path, storage_path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

        logger.info(
            "dicom_file_saved",
            backend="local",
            path=storage_path,
            size=len(content),
            patient_id=patient_id,
            session_id=session_id,
        )
        return storage_path

    async def _save_minio(
        self,
        content: bytes,
        patient_id: str,
        session_id: str,
        sop_instance_uid: str,
    ) -> str:
        object_key = self._build_minio_key(patient_id, session_id, sop_instance_uid)
        client = self._get_minio_client()
        client.put_object(
            settings.minio_bucket,
            object_key,
            BytesIO(content),
            length=len(content),
            content_type="application/dicom",
        )
        logger.info(
            "dicom_file_saved",
            backend="minio",
            object_key=object_key,
            size=len(content),
            patient_id=patient_id,
            session_id=session_id,
        )
        return f"minio://{settings.minio_bucket}/{object_key}"

    def _build_minio_key(self, patient_id: str, session_id: str, sop_instance_uid: str) -> str:
        date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"{date_prefix}/{patient_id}/{session_id}/{sop_instance_uid}.dcm"

    async def retrieve(self, storage_path: str) -> bytes | None:
        if self._backend == "local":
            if os.path.exists(storage_path):
                async with aiofiles.open(storage_path, "rb") as f:
                    return await f.read()
            return None
        elif self._backend == "minio":
            if storage_path.startswith("minio://"):
                _, _, path = storage_path.partition("minio://")
                bucket, _, key = path.partition("/")
                if bucket and key:
                    client = self._get_minio_client()
                    response = client.get_object(bucket, key)
                    return response.read()
            return None
        return None

    async def delete(self, storage_path: str) -> bool:
        if self._backend == "local":
            if os.path.exists(storage_path):
                os.remove(storage_path)
                return True
            return False
        elif self._backend == "minio":
            if storage_path.startswith("minio://"):
                _, _, path = storage_path.partition("minio://")
                bucket, _, key = path.partition("/")
                if bucket and key:
                    client = self._get_minio_client()
                    client.remove_object(bucket, key)
                    return True
            return False
        return False


_dicom_storage: DicomStorageService | None = None


def get_dicom_storage() -> DicomStorageService:
    global _dicom_storage
    if _dicom_storage is None:
        _dicom_storage = DicomStorageService()
    return _dicom_storage
