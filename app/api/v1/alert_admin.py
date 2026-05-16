from fastapi import APIRouter, HTTPException, Depends, Query
from app.security.auth import get_current_active_user, RoleChecker
from app.observability.alert_escalation import get_alert_escalation_manager
from typing import Any, Dict, List, Optional

router = APIRouter(tags=["alert-admin"])

_admin_only = Depends(RoleChecker(["SYSTEM_ADMIN"]))


@router.get("/contacts")
async def get_alert_contacts(current_user: Dict[str, Any] = _admin_only):
    manager = get_alert_escalation_manager()
    return {"contacts": manager.get_contacts()}


@router.put("/contacts")
async def update_alert_contacts(
    contacts: List[Dict[str, Any]],
    current_user: Dict[str, Any] = _admin_only,
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
async def get_active_alerts(current_user: Dict[str, Any] = _admin_only):
    manager = get_alert_escalation_manager()
    return {"alerts": manager.get_active_alerts()}


@router.post("/cusum/calibrate")
async def run_cusum_calibration(
    target_rate: float = Query(0.005, gt=0, le=0.1, description="目标误判率(0-0.1)"),
    daily_cases: int = Query(50, gt=0, le=1000, description="日均病例数(1-1000)"),
    simulation_days: int = Query(30, gt=0, le=365, description="模拟天数(1-365)"),
    current_user: Dict[str, Any] = _admin_only,
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
async def get_cusum_calibration_report(current_user: Dict[str, Any] = _admin_only):
    from app.observability.cusum import CUSUMCalibrator

    report = CUSUMCalibrator.load_calibration_report()
    if report is None:
        raise HTTPException(status_code=404, detail="校准报告不存在，请先执行校准")
    return report
