import asyncio
import base64
import uuid
from datetime import datetime, timezone
from io import BytesIO

import pydicom
import structlog
from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.v1.errors import (
    BadRequestException,
    MissingDICOMTagException,
    NotFoundException,
    ServerErrorException,
)
from app.api.v1.utils import get_actor_id
from app.config.database import AsyncSessionLocal
from app.config.settings import settings
from app.dependencies import get_audit_service, get_session_repo
from app.models.schemas import DICOMStreamRequest, DICOMStreamResponse, DICOMUploadResponse
from app.repositories import SessionRepository
from app.repositories.dicom_image_repository import DicomImageRepository
from app.security.auth import get_current_active_user
from app.services.audit_service import AuditService
from app.services.dicom_storage import get_dicom_storage

logger = structlog.get_logger()
router = APIRouter(tags=["imaging"])


def generate_image_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"img_{timestamp}_{uuid.uuid4().hex[:8]}"


def generate_frame_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"frame_{timestamp}_{uuid.uuid4().hex[:8]}"


REQUIRED_DICOM_TAGS = [
    (0x0010, 0x0010, "PatientName"),
    (0x0010, 0x0020, "PatientID"),
    (0x0020, 0x000D, "StudyInstanceUID"),
    (0x0020, 0x000E, "SeriesInstanceUID"),
    (0x0008, 0x0018, "SOPInstanceUID"),
    (0x0008, 0x0060, "Modality"),
    (0x0028, 0x0010, "Rows"),
    (0x0028, 0x0011, "Columns"),
    (0x0028, 0x0100, "BitsAllocated"),
    (0x7FE0, 0x0010, "PixelData"),
]


def validate_dicom_format(content: bytes) -> tuple[bool, str | None]:
    """DICOM format basic validation (magic word)"""
    if len(content) < 132:
        return False, "File too small"
    if content[128:132] != b"DICM":
        return False, "Invalid DICOM format"
    return True, None


def parse_and_validate_dicom(content: bytes) -> tuple[pydicom.Dataset | None, str | None, list[str]]:
    """Parse DICOM file and validate required tags (single parse)

    Returns:
        (dataset, error_message, missing_tags)
        dataset being None means validation failed
    """
    try:
        ds = pydicom.dcmread(BytesIO(content), force=True)
    except Exception as e:
        return None, f"Failed to parse DICOM: {e!s}", []

    missing_tags = []
    for group, elem, name in REQUIRED_DICOM_TAGS:
        if (group, elem) not in ds:
            missing_tags.append(name)

    if missing_tags:
        return None, f"Missing required DICOM tags: {', '.join(missing_tags)}", missing_tags

    modality = str(ds[0x0008, 0x0060].value).strip()
    if modality != "US":
        return None, f"Modality must be US, got {modality}", [f"Modality={modality}"]

    bits_allocated = int(ds[0x0028, 0x0100].value)
    if bits_allocated <= 0 or bits_allocated > 16:
        return None, f"BitsAllocated must be 1-16, got {bits_allocated}", [f"BitsAllocated={bits_allocated}"]

    return ds, None, []


def _safe_int(value) -> int | None:
    """DICOM 数值标签 → int；IS 类型/字符串都能吃，失败返回 None。

    pydicom 的 IS（整数串）类型不直接是 int；None、空串、非法值都必须安全
    降级——影像索引是旁路，不能因一个标签把上传请求打回。
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _desensitize_patient_id(value: str) -> str:
    if not value:
        return ""
    return value[:1] + "******"


def _desensitize_patient_name(value: str) -> str:
    if not value:
        return ""
    return value[:1] + "******"


def extract_metadata_from_dataset(ds: pydicom.Dataset, content: bytes) -> tuple[dict, dict, str]:
    raw_patient_id = str(getattr(ds, "PatientID", ""))
    raw_patient_name = str(getattr(ds, "PatientName", ""))
    response_metadata = {
        "patient_id": _desensitize_patient_id(raw_patient_id),
        "modality": str(getattr(ds, "Modality", "")),
        "body_part": str(getattr(ds, "BodyPartExamined", "")),
        "acquisition_date": str(getattr(ds, "AcquisitionDate", "")),
    }
    internal_metadata = {
        "patient_name": _desensitize_patient_name(raw_patient_name),
        "study_instance_uid": str(getattr(ds, "StudyInstanceUID", "")),
        "series_instance_uid": str(getattr(ds, "SeriesInstanceUID", "")),
        "sop_instance_uid": str(getattr(ds, "SOPInstanceUID", "")),
        "rows": int(getattr(ds, "Rows", 0)),
        "columns": int(getattr(ds, "Columns", 0)),
        "bits_allocated": int(getattr(ds, "BitsAllocated", 0)),
        "file_size": len(content),
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    return response_metadata, internal_metadata, raw_patient_id


@router.post("/dicom/upload", response_model=DICOMUploadResponse)
async def upload_dicom(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    priority: int = Form(1),
    session_repo: SessionRepository = Depends(get_session_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):

    if priority not in (0, 1, 2):
        raise BadRequestException(
            error_code="E1001",
            error_message=f"priority must be 0 (emergency), 1 (routine), or 2 (teaching), got {priority}",
        )

    session = await session_repo.get_session(session_id)
    if not session:
        raise NotFoundException(error_message=f"Session not found: {session_id}")

    content = await file.read()

    if len(content) > settings.dicom_storage_max_file_size:
        raise BadRequestException(
            error_code="E1004",
            error_message=f"DICOM file size {len(content)} exceeds maximum {settings.dicom_storage_max_file_size}",
        )

    is_valid, error_msg = validate_dicom_format(content)
    if not is_valid:
        raise BadRequestException(error_code="E1004", error_message=error_msg or "Invalid DICOM file")

    # 并发审查 P0-4：dcmread + 标签校验是纯 CPU 同步解析（单份 1~30MB，
    # 典型 5~50ms），在事件循环里做会让并发上传互相排队。offload 到线程池。
    dataset, tags_error, missing_tags = await asyncio.to_thread(
        parse_and_validate_dicom, content
    )
    if dataset is None:
        raise MissingDICOMTagException(error_message=tags_error or "Missing required DICOM tags", detail=",".join(missing_tags))

    response_metadata, internal_metadata, raw_patient_id = extract_metadata_from_dataset(dataset, content)
    internal_metadata = dict(internal_metadata)
    internal_metadata["original_filename"] = file.filename
    internal_metadata["priority"] = priority

    image_id = generate_image_id()

    sop_instance_uid = internal_metadata.get("sop_instance_uid", image_id)
    try:
        storage = get_dicom_storage()
        # storage_id=image_id：落盘路径与 DicomStorageService.path_for_id(image_id)
        # 严格自洽——这是"用 diagnosis_results.image_ids 取回字节"的前提。
        # 旧布局（{date}/{patient}/{session}/{sop}.dcm）保留给不传 ID 的调用方。
        storage_path = await storage.save(
            content=content,
            patient_id=raw_patient_id,
            session_id=session_id,
            sop_instance_uid=sop_instance_uid,
            storage_id=image_id,
        )
        internal_metadata["storage_path"] = storage_path
    except Exception as e:
        logger.error("dicom_storage_save_failed", error=str(e), image_id=image_id)
        raise ServerErrorException(
            error_code="E2000",
            error_message="DICOM file storage failed",
            detail=str(e),
        ) from e

    # 写影像索引（dicom_images）：把"image_id ↔ session ↔ 字节位置"显式化。
    # 失败只告警不阻断上传——影像已经落盘可用，索引缺失只让"按 session 取图"
    # 退化为不可用（管线侧会显式降级），不该把用户的上传请求打回。
    try:
        async with AsyncSessionLocal() as db:
            await DicomImageRepository(db).create(
                image_id=image_id,
                session_id=session_id,
                storage_path=storage_path,
                patient_id=raw_patient_id,
                sop_instance_uid=sop_instance_uid,
                modality=str(getattr(dataset, "Modality", "") or "") or None,
                width=_safe_int(getattr(dataset, "Rows", None)),
                height=_safe_int(getattr(dataset, "Columns", None)),
                frame_count=_safe_int(getattr(dataset, "NumberOfFrames", None)) or 1,
                size_bytes=len(content),
                original_filename=file.filename,
            )
            await db.commit()
    except Exception as e:  # pragma: no cover - 索引是旁路，不阻断上传
        logger.warning("dicom_image_index_write_failed", error=str(e), image_id=image_id)

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="dicom_upload",
        actor=actor_id,
        session_id=session_id,
        target_type="dicom",
        target_id=image_id,
        detail=internal_metadata,
    )

    logger.info(
        "DICOM file uploaded",
        image_id=image_id,
        session_id=session_id,
        file_size=len(content),
    )

    return DICOMUploadResponse(
        image_id=image_id,
        session_id=session_id,
        metadata=response_metadata,
        status="accepted"
    )


@router.post("/dicom/stream", response_model=DICOMStreamResponse)
async def stream_dicom_frame(
    request: DICOMStreamRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    if request.frame_index < 0:
        raise BadRequestException(
            error_code="E1001",
            error_message=f"frame_index must be non-negative, got {request.frame_index}",
        )

    session = await session_repo.get_session(request.session_id)
    if not session:
        raise NotFoundException(error_message=f"Session not found: {request.session_id}")

    try:
        # 并发审查 P1-9：关键帧 base64 常 1~30MB，b64decode 是纯 CPU（每秒几十 MB
        # 量级），放事件循环里会拖慢所有并发请求。offload。
        frame_data = await asyncio.to_thread(base64.b64decode, request.frame_data)
    except Exception as e:
        raise BadRequestException(error_message="Invalid base64 data") from e

    if len(frame_data) >= 132 and frame_data[128:132] == b"DICM":
        if request.is_key_frame:
            _, validation_error, _ = await asyncio.to_thread(
                parse_and_validate_dicom, frame_data
            )
            if validation_error:
                raise BadRequestException(
                    error_code="E1004",
                    error_message=validation_error,
                )
    elif len(frame_data) > 0:
        logger.warning(
            "Streamed frame is not valid DICOM format",
            session_id=request.session_id,
            frame_index=request.frame_index,
        )

    frame_id = generate_frame_id()

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="dicom_stream",
        actor=actor_id,
        session_id=request.session_id,
        target_type="dicom_frame",
        target_id=frame_id,
        detail={
            "frame_index": request.frame_index,
            "is_key_frame": request.is_key_frame,
            "priority": request.priority,
            "frame_size": len(frame_data),
        },
    )

    logger.info(
        "DICOM frame streamed",
        frame_id=frame_id,
        session_id=request.session_id,
        frame_index=request.frame_index,
    )

    return DICOMStreamResponse(
        session_id=request.session_id,
        frame_id=frame_id,
        navigation_hint={
            "available": False,
            "confidence": 0.0,
            "threshold": settings.navigation_confidence_threshold,
        },
        status="accepted"
    )
