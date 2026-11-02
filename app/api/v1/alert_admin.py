from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.config.database import get_db_session
from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey
from app.api.v1.utils import get_actor_id
from sqlalchemy.ext.asyncio import AsyncSession
from app.observability.alert_escalation import get_alert_escalation_manager
from app.security.auth import PermissionChecker, RoleChecker

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
    # A.114 / G18：存到**运行时真正会读的那个路径**，并把"下次启动会采用什么参数"
    # 一并回给调用方 —— 否则管理员调完参数、看到报告，并不知道线上是否生效。
    from app.config.settings import settings as _settings
    from app.observability.cusum.runtime_config import load_runtime_config

    path = getattr(_settings, "obs_cusum_calibration_path", "data/cusum_calibration_report.json")
    calibrator.save_calibration_report(report, path=path)
    cfg, source = load_runtime_config(path)
    report["runtime"] = {
        "path": path,
        "source": source,
        "target_rate": cfg.target_rate,
        "decision_threshold": cfg.decision_threshold,
        "delta": cfg.delta,
        "applies": "next_startup",
    }
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
    db: AsyncSession = Depends(get_db_session),
    current_user: dict[str, Any] = _admin_only,
    # A.88 权限接线②：阈值配置用**权限位**准入（``check_permission``）。
    # 权限位名 ``configure_sg_thresholds`` 在 PERMISSIONS 里只授给 SYSTEM_ADMIN
    # —— 与原白名单 `RoleChecker(["SYSTEM_ADMIN"])` **逐字等价**（不放宽）。
    # 没复用 ``configure_thresholds`` 的理由见 app/security/auth.py 的
    # ADMIN_PERMISSIONS 注释：那条指的是离线可评审的器官阈值，本条会即时改变
    # 对患者的放行/拦截行为，属运行期安全决策。
    _perm: dict[str, Any] = Depends(PermissionChecker("configure_sg_thresholds")),
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

    # A.118 / G43：改运行时安全阈值是**关键操作**（`is_critical_operation` 名单里的
    # `threshold_adjustment`），但 `CriticalOperationAudit` 全仓零生产调用 —— 关键链
    # 一条记录都没有，"谁在何时改了对患者的放行/拦截行为"只能靠普通审计。
    # 这里在下发**成功之后**写关键链（下发失败不写"改过"的假记录）；
    # 审计失败则是**合规事故**：阈值已经生效却无法留痕，必须让调用方明确知道（500）。
    actor = get_actor_id(current_user)
    try:
        from app.observability.audit_chain import CriticalOperationAudit

        await CriticalOperationAudit(db).log_critical_operation(
            operation="threshold_adjustment",
            operator=actor,
            before_value=None,
            after_value=json.dumps(message, sort_keys=True, ensure_ascii=False),
            detail={
                "action": message["action"],
                "organ": message.get("organ"),
                "threshold": message.get("threshold"),
                "stream": RedisKey.SG_THRESHOLD_STREAM,
            },
        )
        await db.commit()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "阈值已下发生效，但关键审计写入失败（合规事故，需立即排查）："
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc

    return {
        "status": "published",
        "stream": RedisKey.SG_THRESHOLD_STREAM,
        "message": message,
        "audit_chain": "critical",
        "audited_by": actor,
    }
