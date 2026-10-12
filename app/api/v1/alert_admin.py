from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey
from app.observability.alert_escalation import get_alert_escalation_manager
from app.security.auth import RoleChecker

router = APIRouter(tags=["alert-admin"])

_admin_only = Depends(RoleChecker(["SYSTEM_ADMIN"]))


@router.get("/contacts")
async def get_alert_contacts(current_user: dict[str, Any] = _admin_only):
    manager = get_alert_escalation_manager()
    return {"contacts": manager.get_contacts()}


@router.put("/contacts")
async def update_alert_contacts(
    contacts: list[dict[str, Any]],
    current_user: dict[str, Any] = _admin_only,
):
    import json

    manager = get_alert_escalation_manager()
    contacts_json = json.dumps(contacts)
    success = await manager.update_contacts(contacts_json)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="告警联系人可达性验证失败，已回滚至旧配置",
        )
    return {"status": "updated", "count": len(contacts)}


@router.get("/active-alerts")
async def get_active_alerts(current_user: dict[str, Any] = _admin_only):
    manager = get_alert_escalation_manager()
    return {"alerts": manager.get_active_alerts()}


@router.post("/cusum/calibrate")
async def run_cusum_calibration(
    target_rate: float = Query(0.005, gt=0, le=0.1, description="目标误判率(0-0.1)"),
    daily_cases: int = Query(50, gt=0, le=1000, description="日均病例数(1-1000)"),
    simulation_days: int = Query(30, gt=0, le=365, description="模拟天数(1-365)"),
    current_user: dict[str, Any] = _admin_only,
):
    from app.observability.cusum import CUSUMCalibrator

    calibrator = CUSUMCalibrator(
        target_rate=target_rate,
        daily_cases=daily_cases,
        simulation_days=simulation_days,
    )
    results = calibrator.calibrate()
    report = calibrator.generate_calibration_report(results)
    calibrator.save_calibration_report(report)
    return report


@router.get("/cusum/calibration-report")
async def get_cusum_calibration_report(current_user: dict[str, Any] = _admin_only):
    from app.observability.cusum import CUSUMCalibrator

    report = CUSUMCalibrator.load_calibration_report()
    if report is None:
        raise HTTPException(status_code=404, detail="校准报告不存在，请先执行校准")
    return report


class SGThresholdUpdateRequest(BaseModel):
    """SG 阈值更新消息（wiring-audit P1-8：为消费流补生产者入口）。

    payload 契约与 safety_gateway/threshold_subscriber.py
    的 _handle_threshold_update 一致：action=refresh 无需其余字段；
    action=update 需 organ+threshold；action=delete 需 organ。
    """
    action: Literal["refresh", "update", "delete"]
    organ: str | None = None
    threshold: float | None = None
    disease_type: str | None = None


@router.post("/sg-threshold")
async def publish_sg_threshold_update(
    request: SGThresholdUpdateRequest,
    current_user: dict[str, Any] = _admin_only,
):
    """向 SafetyGateway 阈值更新流（sg:threshold:stream）投递更新消息。

    SG 侧以 Redis Streams 消费（组 sg-threshold-group），离线期间消息
    不丢失。历史上该流全库无生产者，SG 的订阅链路形同虚设。
    """
    if request.action == "update" and (
        not request.organ or request.threshold is None
    ):
        raise HTTPException(
            status_code=422,
            detail="action=update 需要同时提供 organ 与 threshold",
        )
    if request.action == "delete" and not request.organ:
        raise HTTPException(status_code=422, detail="action=delete 需要提供 organ")

    message: dict[str, Any] = {"action": request.action}
    if request.organ:
        message["organ"] = request.organ
    if request.threshold is not None:
        message["threshold"] = request.threshold
    if request.disease_type:
        message["disease_type"] = request.disease_type

    import json

    try:
        client = await RedisPool.get_client()
        await client.xadd(
            RedisKey.SG_THRESHOLD_STREAM,
            {"payload": json.dumps(message)},
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis 不可用：{exc}") from exc

    return {"status": "published", "stream": RedisKey.SG_THRESHOLD_STREAM, "message": message}
