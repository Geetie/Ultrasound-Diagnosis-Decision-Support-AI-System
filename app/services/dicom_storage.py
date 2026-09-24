"""DICOM文件存储服务 - 支持本地文件系统和MinIO后端"""
import os
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
        logger.warning("dicom_phi_strip_failed", error=str(e))
        return content, False


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

        stripped_content, was_stripped = strip_dicom_phi(content, patient_id)
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
