import asyncio
import base64
import uuid
from datetime import datetime, timezone
from io import BytesIO

import pydicom
import structlog
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.errors import (
    BadRequestException,
    MissingDICOMTagException,
    NotFoundException,
    ServerErrorException,
)
from app.api.v1.utils import get_actor_id
from app.config.database import get_db_session
from app.config.settings import settings
from app.dependencies import (
    get_audit_service,
    get_dicom_image_repo,
    get_session_repo,
)
from app.models.schemas import (
    DICOMImageListResponse,
    DICOMImageMeta,
    DICOMStreamRequest,
    DICOMStreamResponse,
    DICOMUploadResponse,
)
from app.repositories import SessionRepository
from app.repositories.dicom_image_repository import DicomImageRepository
from app.security.auth import get_current_active_user
from app.services.audit_service import AuditService
from app.services.dicom_storage import get_dicom_storage, pseudonymize_patient_id

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
    """DICOM 头部（魔数）基础校验。

    A.52 端到端摸排：这些 message 会**原样展示给医生**（`File too small` /
    `Missing required DICOM tags` 实测出现在装载区）——一律写成可读中文。
    """
    if len(content) < 132:
        return False, "文件过小：不足 DICOM 文件头 132 字节（疑似非影像文件或已损坏）"
    if content[128:132] != b"DICM":
        return False, "不是 DICOM 文件：缺少 DICM 魔数（请确认导出的确实是 DICOM）"
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
        logger.warning("parse_and_validate_dicom: 未预期异常，按原逻辑降级", exc_info=True)
        return None, f"DICOM 解析失败：{e!s}", []

    missing_tags = []
    for group, elem, name in REQUIRED_DICOM_TAGS:
        if (group, elem) not in ds:
            missing_tags.append(name)

    if missing_tags:
        return None, f"缺少 DICOM 必填字段：{', '.join(missing_tags)}", missing_tags

    modality = str(ds[0x0008, 0x0060].value).strip()
    if modality != "US":
        return None, f"影像模态必须是超声（US），实际为 {modality}", [f"Modality={modality}"]

    bits_allocated = int(ds[0x0028, 0x0100].value)
    if bits_allocated <= 0 or bits_allocated > 16:
        return (
            None,
            f"位深不受支持：BitsAllocated 须为 1~16，实际 {bits_allocated}",
            [f"BitsAllocated={bits_allocated}"],
        )

    return ds, None, []


def _normalize_odd_frame_multiframe(
    content: bytes, dataset: pydicom.Dataset
) -> tuple[bytes, pydicom.Dataset, str | None]:
    """把「原生多帧 + 每帧字节数为奇」的文件规范化成合规编码（A.52）。

    返回 ``(内容, 数据集, 规范化方式)``；不需要规范化时方式为 ``None``（内容原样返回）。

    **为什么必须做**：DICOM 的值长度必须为偶数，而原生多帧要求
    ``像素数据长度 == 帧数 × 每帧字节数``。每帧字节数
    ``= Rows × Columns × BitsAllocated/8 × SamplesPerPixel`` 为奇时
    （例如 8 位 549×389 = 213561），写入端只能补 1 个填充字节 →
    前端 `总长/帧数` 不整除 → dicom-image-loader 抛 `Invalid frame length`、
    影像区黑屏。后端口径（pydicom 按帧数截取）不受影响，所以这个坑只在浏览器暴露。

    **为什么选 8→16 位重写**：①**零像素改动** —— 每个值原样放进 16 位字
    （BitsStored 仍为 8、HighBit=7），不做插值也不补边，Rows/Columns/PixelSpacing/
    窗宽窗位全部保持不变，测量与判读口径完全不受影响；②补边方案（补 1 列/行）会
    改变图像尺寸 → 影响 mm 测量，不可接受；③封装（压缩）方案需要 RLE/JPEG 编解码器，
    代价与许可风险更高。
    """
    try:
        frames = int(getattr(dataset, "NumberOfFrames", 1) or 1)
    except (TypeError, ValueError):
        logger.warning("_normalize_odd_frame_multiframe: 未预期异常，按原逻辑降级", exc_info=True)
        return content, dataset, None
    if frames < 2:
        return content, dataset, None

    rows = int(getattr(dataset, "Rows", 0) or 0)
    cols = int(getattr(dataset, "Columns", 0) or 0)
    bits = int(getattr(dataset, "BitsAllocated", 0) or 0)
    samples = int(getattr(dataset, "SamplesPerPixel", 1) or 1)
    bytes_per_sample = bits // 8
    if not rows or not cols or bytes_per_sample <= 0:
        return content, dataset, None
    frame_bytes = rows * cols * bytes_per_sample * samples
    if frame_bytes % 2 == 0:
        return content, dataset, None

    # 目前只处理 8 位（唯一可能产生「奇帧长」的位深：16/32 位必定为偶）
    if bits != 8 or samples != 1:
        logger.warning(
            "dicom_multiframe_odd_frame_length_unsupported",
            bits_allocated=bits,
            samples_per_pixel=samples,
            frame_bytes=frame_bytes,
        )
        return content, dataset, None

    import copy as _copy

    normalized_ds = _copy.deepcopy(dataset)
    array = dataset.pixel_array  # (frames, rows, cols) uint8
    normalized_ds.BitsAllocated = 16
    normalized_ds.BitsStored = 8
    normalized_ds.HighBit = 7
    normalized_ds.PixelRepresentation = 0
    normalized_ds.PixelData = array.astype("<u2").tobytes()

    buffer = BytesIO()
    pydicom.dcmwrite(buffer, normalized_ds, enforce_file_format=True)
    normalized = buffer.getvalue()
    logger.info(
        "dicom_multiframe_normalized_for_display",
        frames=frames,
        old_frame_bytes=frame_bytes,
        new_frame_bytes=frame_bytes * 2,
        reason="odd_frame_length_to_16bit",
    )
    return normalized, normalized_ds, "odd_frame_length_to_16bit"


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
        logger.warning("_safe_int: 未预期异常，按原逻辑降级", exc_info=True)
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
    db: AsyncSession = Depends(get_db_session),
    current_user: dict | None = Depends(get_current_active_user),
):

    if priority not in (0, 1, 2):
        raise BadRequestException(
            error_code="E1001",
            error_message=f"优先级取值非法（须为 0 急诊 / 1 常规 / 2 教学），实际 {priority}",
        )

    session = await session_repo.get_session(session_id)
    if not session:
        raise NotFoundException(error_message=f"检查会话不存在：{session_id}")

    content = await file.read()

    if len(content) > settings.dicom_storage_max_file_size:
        raise BadRequestException(
            error_code="E1004",
            error_message=(
                f"影像文件过大：{len(content)} 字节，超过上限 "
                f"{settings.dicom_storage_max_file_size} 字节"
            ),
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
        raise MissingDICOMTagException(
            error_message=tags_error or "缺少 DICOM 必填字段", detail=",".join(missing_tags)
        )

    # A.52 鲁棒性：原生（非封装）多帧且**每帧字节数为奇**时，DICOM 值长度必须为偶 →
    # 必然多出 1 个填充字节 → `总长/帧数` 不整除 → 前端 dicom-image-loader 直接抛
    # `Invalid frame length`、影像区只剩黑屏（真机实测）。这里在**入库侧**把它规范化成
    # 合规编码，且**不改变任何像素值**（8 位 → 16 位重写，值一一对应，Rows/Columns/
    # PixelSpacing 全不动，测量与判读口径不受影响）。
    content, dataset, normalized = await asyncio.to_thread(
        _normalize_odd_frame_multiframe, content, dataset
    )

    response_metadata, internal_metadata, raw_patient_id = extract_metadata_from_dataset(dataset, content)
    internal_metadata = dict(internal_metadata)
    internal_metadata["original_filename"] = file.filename
    internal_metadata["priority"] = priority
    if normalized:
        # A.52：入库时做过显示规范化 —— 留痕供审计（原始像素值未改，仅编码位深）
        internal_metadata["dicom_normalized"] = normalized

    # A.88：落盘与索引**只用带盐不可逆假名**，绝不用 raw_patient_id。
    # 原因：dicom_storage.strip_dicom_phi 会把 (0010,0020) 写成传进去的
    # patient_id（通用写入原语，语义不动），所以调用侧传原文 = 把 PHI 写回文件，
    # 脱敏白做。假名保留"同患者可关联"（相似病例 scope=self / 影像向量隔离
    # 靠 patient_id 相等），但不可逆、不含原文。
    # 纯展示用的 X****** 形态仍在 response_metadata["patient_id"]，两者互不替代。
    anonymized_patient_id = pseudonymize_patient_id(raw_patient_id)
    # PHI 不得进审计 detail（AGENTS.md #10）：只记一个布尔值
    # "这条影像带上了病人归属标识"，便于运维核对，而不留任何可反推线索
    # （原文与假名本身都不进 detail）。
    internal_metadata["patient_id_anonymized"] = bool(anonymized_patient_id)

    image_id = generate_image_id()

    sop_instance_uid = internal_metadata.get("sop_instance_uid", image_id)
    try:
        storage = get_dicom_storage()
        # storage_id=image_id：落盘路径与 DicomStorageService.path_for_id(image_id)
        # 严格自洽——这是"用 diagnosis_results.image_ids 取回字节"的前提。
        # 旧布局（{date}/{patient}/{session}/{sop}.dcm）保留给不传 ID 的调用方。
        storage_path = await storage.save(
            content=content,
            patient_id=anonymized_patient_id,
            session_id=session_id,
            sop_instance_uid=sop_instance_uid,
            storage_id=image_id,
        )
        internal_metadata["storage_path"] = storage_path
    except Exception as e:
        logger.error("dicom_storage_save_failed", error=str(e), image_id=image_id)
        raise ServerErrorException(
            error_code="E2000",
            error_message="影像文件存储失败",
            detail=str(e),
        ) from e

    # 写影像索引（dicom_images）：把"image_id ↔ session ↔ 字节位置"显式化。
    # 用 Depends(get_db_session) 注入的会话（由框架统一提交）——**不手动 commit**
    # （tests/unit/sec/test_audit.py::test_imaging_upload_no_manual_commit 守着这条：
    #  手动 commit 会与依赖的提交重复，正是当初双提交事故的形状）。
    # 失败只告警不阻断上传——影像已落盘可用，索引缺失只让"按 session 取图"
    # 退化为不可用（管线侧显式降级），不该把用户的上传请求打回。
    try:
        await DicomImageRepository(db).create(
            image_id=image_id,
            session_id=session_id,
            storage_path=storage_path,
            patient_id=anonymized_patient_id,
            sop_instance_uid=sop_instance_uid,
            modality=str(getattr(dataset, "Modality", "") or "") or None,
            width=_safe_int(getattr(dataset, "Rows", None)),
            height=_safe_int(getattr(dataset, "Columns", None)),
            frame_count=_safe_int(getattr(dataset, "NumberOfFrames", None)) or 1,
            size_bytes=len(content),
            original_filename=file.filename,
        )
    except Exception as e:  # pragma: no cover - 索引是旁路，不阻断上传
        logger.warning("dicom_image_index_write_failed", error=str(e), image_id=image_id)
        # 必须显式 rollback：flush 失败后该 session 处于 PendingRollbackError 状态，
        # 框架后续提交时会抛 "This Session's transaction has been rolled back"
        # 把整个上传请求打成 500 —— 实测踩过（DB 不可达时必现）。
        # 这里只回滚本请求内的写（上传路径没有其他 DB 写入），不会丢数据。
        try:
            await db.rollback()
        except Exception as rb_exc:  # pragma: no cover - rollback 自身失败属环境异常
            logger.warning("dicom_image_index_rollback_failed",
                           error=str(rb_exc), image_id=image_id)

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


# ----------------------------------------------------------------------
# 影像回取（2026-10-06，A.30/A.31）
#
# 为什么现在才补：上传端点一直在，但**没有任何 GET**——浏览器显示影像
# 只能用上传瞬间的浏览器本地副本（wadouri fileManager），刷新页面后影像区
# 回到占位底色，且"显示的字节"与"管线用的字节"（服务端那份，已 PHI 剥离）
# 不是同一份。imagingService.ts 顶部注释从 2026-10-05 起就把这标注为
# 「真实的 API 缺口」，本节闭合它。
#
# 显示路径刻意**不走 Cornerstone 的远程加载**（wadouri:http://... 需要给
# 加载器配鉴权头、且加载器内部 XHR 拿不到 axios 的 Bearer 注入），而是：
# 前端用 axios（自动带 Bearer）把字节取回 → wadouri.fileManager.add(blob)
# → 走已验证的本地显示路径。这样鉴权天然一致，也不依赖加载器内部实现。
# ----------------------------------------------------------------------


@router.get("/dicom", response_model=DICOMImageListResponse)
async def list_session_dicom(
    session_id: str = Query(..., description="检查会话 ID"),
    limit: int = Query(50, ge=1, le=200),
    image_repo: DicomImageRepository = Depends(get_dicom_image_repo),
    session_repo: SessionRepository = Depends(get_session_repo),
    current_user: dict | None = Depends(get_current_active_user),
):
    """按会话列出影像索引（最早优先）——工作区「继续检查」时恢复影像用。"""
    session = await session_repo.get_session(session_id)
    if not session:
        raise NotFoundException(error_message=f"检查会话不存在：{session_id}")

    rows = await image_repo.list_by_session(session_id, limit=limit)
    return DICOMImageListResponse(
        session_id=session_id,
        count=len(rows),
        images=[
            DICOMImageMeta(
                image_id=row.image_id,
                session_id=row.session_id,
                modality=row.modality,
                width=row.width,
                height=row.height,
                frame_count=row.frame_count,
                size_bytes=row.size_bytes,
                original_filename=row.original_filename,
                created_at=row.created_at,
            )
            for row in rows
        ],
    )


@router.get("/dicom/{image_id}")
async def retrieve_dicom(
    image_id: str,
    image_repo: DicomImageRepository = Depends(get_dicom_image_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    """按 image_id 取回 DICOM 字节（``application/dicom``）。

    服务端这份与管线用是**同一份**（已 PHI 剥离）：显示它，医生看到的
    就是 AI 看到的。404 时**如实报 404**——不返回占位图或空字节。
    """
    row = await image_repo.get(image_id)
    if not row:
        raise NotFoundException(error_message=f"影像不存在：{image_id}")

    storage = get_dicom_storage()
    content = await storage.retrieve(row.storage_path)
    if not content:
        # 索引在、字节没了：这是真实的存储损坏，不能伪装成"图是空的"
        logger.error(
            "dicom_retrieve_bytes_missing", image_id=image_id, storage_path=row.storage_path
        )
        raise NotFoundException(
            error_message=f"影像字节不可用：{image_id}",
            detail="索引存在但影像字节缺失 —— 请检查 dicom_storage 后端",
        )

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="dicom_retrieve",
        actor=actor_id,
        session_id=row.session_id,
        target_type="dicom",
        target_id=image_id,
        detail={"size_bytes": len(content), "modality": row.modality},
    )

    logger.info(
        "DICOM file retrieved",
        image_id=image_id,
        session_id=row.session_id,
        file_size=len(content),
    )
    return Response(
        content=content,
        media_type="application/dicom",
        headers={"Content-Disposition": f'inline; filename="{image_id}.dcm"'},
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
            error_message=f"frame_index 不能为负，实际 {request.frame_index}",
        )

    session = await session_repo.get_session(request.session_id)
    if not session:
        raise NotFoundException(error_message=f"检查会话不存在：{request.session_id}")

    try:
        # 并发审查 P1-9：关键帧 base64 常 1~30MB，b64decode 是纯 CPU（每秒几十 MB
        # 量级），放事件循环里会拖慢所有并发请求。offload。
        frame_data = await asyncio.to_thread(base64.b64decode, request.frame_data)
    except Exception as e:
        raise BadRequestException(error_message="base64 数据无效") from e

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
