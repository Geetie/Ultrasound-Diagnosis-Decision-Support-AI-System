from fastapi import APIRouter, Depends
from typing import Optional
from datetime import datetime, timezone, timedelta
import secrets
import structlog

from app.models.schemas import (
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceRecoveryResponse,
    DeviceRemoteRecoveryResponse,
    DeviceDeregisterResponse,
)
from app.api.v1.errors import NotFoundException, BadRequestException
from app.dependencies import get_device_repo, get_audit_service
from app.api.v1.utils import get_actor_id
from app.repositories import DeviceRepository
from app.services.audit_service import AuditService
from app.security.auth import RoleChecker

logger = structlog.get_logger()
router = APIRouter(tags=["device-management"])

admin_only = RoleChecker(allowed_roles=["SYSTEM_ADMIN"])


@router.post("/register", response_model=DeviceRegisterResponse)
async def register_device(
    request: DeviceRegisterRequest,
    device_repo: DeviceRepository = Depends(get_device_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: Optional[dict] = Depends(admin_only),
):
    existing = await device_repo.get_device_by_name(request.device_name)
    if existing and existing.status == "deregistered":
        raise BadRequestException(
            error_code="E1001",
            error_message=f"Device name '{request.device_name}' was previously deregistered and cannot be reused per security policy",
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


@router.post("/{device_id}/deregister", response_model=DeviceDeregisterResponse)
async def deregister_device(
    device_id: str,
    device_repo: DeviceRepository = Depends(get_device_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: Optional[dict] = Depends(admin_only),
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
    current_user: Optional[dict] = Depends(admin_only),
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
    current_user: Optional[dict] = Depends(admin_only),
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
