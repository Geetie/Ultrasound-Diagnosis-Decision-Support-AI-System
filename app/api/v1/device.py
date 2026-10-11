
import secrets
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException

from app.api.v1.errors import BadRequestException, NotFoundException
from app.api.v1.utils import get_actor_id
from app.config.settings import settings
from app.dependencies import get_audit_service, get_device_repo
from app.models.database import Device
from app.models.schemas import (
    DeviceCheckInResponse,
    DeviceDeregisterResponse,
    DeviceHealthProbeRequest,
    DeviceHealthProbeResponse,
    DeviceHeartbeatRequest,
    DeviceHeartbeatResponse,
    DeviceRecoveryResponse,
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceRemoteRecoveryResponse,
)
from app.repositories import DeviceRepository
from app.security.auth import RoleChecker
from app.services.audit_service import AuditService

logger = structlog.get_logger()
router = APIRouter(tags=["device-management"])

admin_only = RoleChecker(allowed_roles=["SYSTEM_ADMIN"])


async def _authenticate_device(
    x_device_id: str = Header(default=""),
    x_device_token: str = Header(default=""),
    device_repo: DeviceRepository = Depends(get_device_repo),
) -> Device:
    """设备头认证（X-Device-Id / X-Device-Token）。

    X-Device-Token 携带原始 API Key，走 ``authenticate_device`` 的
    哈希校验 + active 状态检查 + last-seen 触碰（与 WS 设备路径同源）。
    服务端仅存 API Key 哈希，无法重建 edge 端 HKDF 会话密钥，故 HTTP
    设备端点不接受 HMAC 短期 token（edge/device_auth.py 预留能力）。
    """
    if not x_device_id or not x_device_token:
        raise HTTPException(
            status_code=401,
            detail="Device credentials required (X-Device-Id / X-Device-Token)",
        )
    device = await device_repo.authenticate_device(x_device_id, x_device_token)
    if device is None:
        raise HTTPException(status_code=401, detail="Invalid device credentials")
    return device


@router.post("/register", response_model=DeviceRegisterResponse)
async def register_device(
    request: DeviceRegisterRequest,
    device_repo: DeviceRepository = Depends(get_device_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: dict | None = Depends(admin_only),
):
    existing = await device_repo.get_device_by_name(request.device_name)
    if existing:
        # device_name 是设备身份（checkin/heartbeat 按名查询），必须全局唯一：
        # deregistered 名复用违反安全策略；active 名复用则会造成同名多行，
        # 让 get_device_by_name 的 scalar_one_or_none 炸成 500（A.49 探针实测）。
        if existing.status == "deregistered":
            raise BadRequestException(
                error_code="E1001",
                error_message=f"Device name '{request.device_name}' was previously deregistered and cannot be reused per security policy",
            )
        raise BadRequestException(
            error_code="E1001",
            error_message=f"Device name '{request.device_name}' is already in use by device {existing.device_id}",
        )

    device, raw_api_key = await device_repo.register_device(
        device_name=request.device_name,
        device_type=request.device_type,
        location=request.location,
        jetpack_version=request.jetpack_version,
    )

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="device_register",
        actor=actor_id,
        session_id=None,
        target_type="device",
        target_id=device.device_id,
        detail={
            "device_name": request.device_name,
            "device_type": request.device_type,
            "location": request.location,
        },
    )

    logger.info(
        "Device registered",
        device_id=device.device_id,
        device_name=request.device_name,
    )

    return DeviceRegisterResponse(
        device_id=device.device_id,
        api_key=raw_api_key,
        status=device.status,
    )


@router.post("/checkin", response_model=DeviceCheckInResponse)
async def checkin_device(
    device: Device = Depends(_authenticate_device),
):
    """设备自通告（wiring-audit P0-4）。

    设备开机后凭 api_key 声明上线。供给语义（创建 device 记录 + 签发
    api_key）仍属管理员的 POST /register，二者不混用——历史上 edge 对
    /register 发 {device_id} 单字段请求，与 DeviceRegisterRequest 契约
    双双错配（422/401/403），导致设备系统性假隔离。
    """
    logger.info("device_checked_in", device_id=device.device_id)
    return DeviceCheckInResponse(
        device_id=device.device_id, status=device.status, registered=True
    )


@router.post("/heartbeat", response_model=DeviceHeartbeatResponse)
async def device_heartbeat(
    request: DeviceHeartbeatRequest,
    device: Device = Depends(_authenticate_device),
):
    """设备 HTTP 心跳（edge 15s 间隔；authenticate_device 内已触碰 last-seen）。"""
    if request.device_id != device.device_id:
        raise BadRequestException(
            error_code="E1001",
            error_message="heartbeat device_id does not match authenticated device",
        )
    return DeviceHeartbeatResponse(
        device_id=device.device_id,
        status="ok",
        server_time=datetime.now(timezone.utc).isoformat(),
    )


@router.post("/health", response_model=DeviceHealthProbeResponse)
async def device_health_probe(
    request: DeviceHealthProbeRequest,
    device: Device = Depends(_authenticate_device),
):
    """设备健康探测（edge 1s 周期，隔离期恢复判定依据）。"""
    if request.device_id != device.device_id:
        raise BadRequestException(
            error_code="E1001",
            error_message="health probe device_id does not match authenticated device",
        )
    return DeviceHealthProbeResponse(
        device_id=device.device_id,
        status="ok",
        server_time=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/ota/{package}/{version}")
async def download_ota_package(
    package: str,
    version: str,
    device: Device = Depends(_authenticate_device),
):
    """OTA 包下载（edge/ota_client.py 契约位，A.50.8 E7 落地）。

    - ``settings.ota_package_dir`` 未配置 → 501（保持契约位可见）；
    - 配置后从 ``{dir}/{package}/{version}/{package}-{version}.bin`` 分发，
      FileResponse 原生支持 Range（edge 断点续传 200/206）；
    - 已过设备认证（X-Device 头），包不存在 → 404 供 edge 归因。
    """
    from pathlib import Path

    from fastapi.responses import FileResponse

    raw_dir = (settings.ota_package_dir or "").strip()
    if not raw_dir:
        raise HTTPException(
            status_code=501,
            detail=f"OTA distribution is not deployed (package={package}, version={version})",
        )
    base = Path(raw_dir).expanduser()
    pkg_file = base / package / version / f"{package}-{version}.bin"
    if not pkg_file.is_file():
        raise NotFoundException(
            error_message=f"OTA package not found: {package}@{version}"
        )
    return FileResponse(
        pkg_file,
        media_type="application/octet-stream",
        filename=pkg_file.name,
    )


@router.post("/{device_id}/deregister", response_model=DeviceDeregisterResponse)
async def deregister_device(
    device_id: str,
    device_repo: DeviceRepository = Depends(get_device_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: dict | None = Depends(admin_only),
):
    device = await device_repo.get_device(device_id)
    if not device:
        raise NotFoundException(error_message=f"Device not found: {device_id}")

    if device.status == "deregistered":
        raise BadRequestException(
            error_code="E1001",
            error_message=f"Device {device_id} is already deregistered",
        )

    updated = await device_repo.update_device_status(device_id, "deregistered")

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="device_deregister",
        actor=actor_id,
        session_id=None,
        target_type="device",
        target_id=device_id,
        detail={"previous_status": device.status},
    )

    logger.info("Device deregistered", device_id=device_id)

    return DeviceDeregisterResponse(device_id=device_id, status=updated.status)


@router.post("/{device_id}/recovery", response_model=DeviceRecoveryResponse)
async def recovery_device(
    device_id: str,
    device_repo: DeviceRepository = Depends(get_device_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: dict | None = Depends(admin_only),
):
    device = await device_repo.get_device(device_id)
    if not device:
        raise NotFoundException(error_message=f"Device not found: {device_id}")

    if device.status == "active":
        raise BadRequestException(
            error_code="E1001",
            error_message=f"Device {device_id} is already active",
        )

    if device.status == "deregistered":
        raise BadRequestException(
            error_code="E1001",
            error_message=f"Device {device_id} is permanently deregistered and cannot be recovered",
        )

    updated = await device_repo.update_device_status(device_id, "active")

    recovery_token = secrets.token_urlsafe(32)
    expires_at_dt = datetime.now(timezone.utc) + timedelta(hours=1)
    await device_repo.store_recovery_token(device_id, recovery_token, expires_at_dt)
    expires_at = expires_at_dt.isoformat()

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="device_recovery",
        actor=actor_id,
        session_id=None,
        target_type="device",
        target_id=device_id,
        detail={"previous_status": device.status},
    )

    logger.info("Device recovered", device_id=device_id)

    return DeviceRecoveryResponse(
        device_id=device_id,
        status=updated.status,
        recovery_token=recovery_token,
        expires_at=expires_at,
    )


@router.post("/{device_id}/remote-recovery", response_model=DeviceRemoteRecoveryResponse)
async def remote_recovery_device(
    device_id: str,
    device_repo: DeviceRepository = Depends(get_device_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: dict | None = Depends(admin_only),
):
    device = await device_repo.get_device(device_id)
    if not device:
        raise NotFoundException(error_message=f"Device not found: {device_id}")

    if device.status != "quarantined":
        raise BadRequestException(
            error_code="E1001",
            error_message=f"Remote recovery is only available for quarantined devices, current status: {device.status}",
        )

    updated = await device_repo.update_device_status(device_id, "active")

    new_api_key = secrets.token_urlsafe(32)
    await device_repo.store_new_api_key(device_id, new_api_key)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="device_remote_recovery",
        actor=actor_id,
        session_id=None,
        target_type="device",
        target_id=device_id,
        detail={"previous_status": "quarantined"},
    )

    logger.info("Device remote recovered", device_id=device_id)

    return DeviceRemoteRecoveryResponse(
        device_id=device_id,
        status=updated.status,
        new_api_key=new_api_key,
        expires_at=expires_at,
    )
