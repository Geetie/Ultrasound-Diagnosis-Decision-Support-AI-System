import structlog
from datetime import datetime, timezone
from fastapi import HTTPException
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.errors import BadRequestException
from app.api.v1.utils import get_actor_id
from app.config.database import get_db_session
from app.config.settings import settings
from app.config.redis_keys import RedisKey
from app.dependencies import get_audit_service
from app.observability.alert_escalation import get_alert_escalation_manager
from app.observability.cache_monitor import get_cache_monitor
from app.observability.cusum import AISafetyCircuitBreaker
from app.observability.metrics_collector import get_metrics_collector
from app.security.auth import PermissionChecker, get_current_active_user
from app.services.cold_archive import ColdArchiveService
from app.services.correction_rate_monitor import assess_recent_corrections
from app.models.schemas import CorrectionRateProxyResponse

logger = structlog.get_logger()

# P0-5（15 号计划）：/internal/obs/* 端点此前无认证——暴露指标仪表盘、
# 冷归档状态与**可触发归档运行**的 POST 端点。加角色认证：系统管理员 +
# 质控管理员（归档操作），只读仪表盘同样需登录。
#
# A.88 配置接线②：改为按**权限位**准入（``check_permission`` 的生产消费者）。
# 角色集合由 ``RoleChecker.for_permission("view_qc_report")`` 从 PERMISSIONS
# 推导 = ["QUALITY_CONTROL", "SYSTEM_ADMIN"]，与原硬编码白名单**逐字相同**
# （零行为漂移；403 文案与错误码 E1008 也不变）。
_require_admin = Depends(PermissionChecker("view_qc_report"))

router = APIRouter(
    tags=["observability-internal"],
    dependencies=[_require_admin],
)


@router.get("/dashboard")
async def dashboard_data():
    collector = get_metrics_collector()
    breaker = AISafetyCircuitBreaker.get_instance()
    alert_mgr = get_alert_escalation_manager()
    cache_mon = get_cache_monitor()

    gpu = _read_gpu_metrics(collector)
    hallucination = _read_hallucination_metrics(collector)
    latency = _read_latency_metrics(collector)
    service = _read_service_metrics(collector)
    cache = _read_cache_metrics(cache_mon)

    breaker_info = breaker.suspension_info
    breaker_data = {
        "state": breaker_info.get("state", "unknown"),
        # A.107 / N2：此前写死 None，运维看不到熔断进度；真值在 monitor 状态里。
        # 取不到时保持 None（看板显示 unknown），不用 0.0 冒充"没有累计"。
        "cusum_s_plus": breaker.monitor_s_plus(),
        "active_alerts": len(alert_mgr.get_active_alerts()),
    }

    return {
        "gpu": gpu,
        "hallucination": hallucination,
        "latency": latency,
        "service": service,
        "cache": cache,
        "breaker": breaker_data,
    }


@router.get("/cold-archive")
async def cold_archive_status(
    category: str | None = Query(default=None, description="audit_logs | session_archives"),
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
):
    """T-GB-SEC-02：冷归档清单与批次总览（热/冷分层的唯一可观测入口）。

    返回按 category+status 的批次统计，以及最近的清单（含 checksum 与
    记录数），运维据此确认"冷层是否真的存住了"。
    """
    service = ColdArchiveService(db)
    stats = await service.stats()
    manifests = await service.list_manifests(category=category, limit=limit)
    return {
        "adapter": service.storage.name,
        "hot_retention_days": settings.audit_hot_retention_days,
        "stats": stats,
        "manifests": [
            {
                "manifest_id": m.manifest_id,
                "category": m.category,
                "status": m.status,
                "object_key": m.object_key,
                "record_count": m.record_count,
                "size_bytes": m.size_bytes,
                "checksum": m.checksum,
                "adapter": m.adapter,
                "archived_at": m.archived_at.isoformat() if m.archived_at else None,
                "purged_at": m.purged_at.isoformat() if m.purged_at else None,
                "message": m.message,
            }
            for m in manifests
        ],
    }


@router.get("/restore-rate")
async def restore_rate_status():
    """T-G1-SEC-02：脱敏还原频率监控（阈值、告警数与最近一次告警）。"""
    from app.security.restore_rate_monitor import get_restore_rate_monitor

    return get_restore_rate_monitor().stats()


@router.post("/cold-archive/run")
async def run_cold_archive(
    category: str = Query(
        default="audit_logs", description="audit_logs | session_archives"
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """手动触发一次冷归档（运维用；等价于周期任务中的冷层步骤）。"""
    service = ColdArchiveService(db)
    if category == "session_archives":
        result = await service.archive_session_archives()
    else:
        result = await service.archive_audit_logs()
    return result.as_dict()


@router.get("/correction-rate")
async def correction_rate(
    window_days: int = Query(default=None, ge=1, le=365),
    db: AsyncSession = Depends(get_db_session),
) -> CorrectionRateProxyResponse:
    """05-3 S0：医生改判率只读端点（接通 correction_rate_monitor 的唯一生产消费者）。

    ⚠️ 口径声明（不许删）：本端点返回的是**改判率代理指标**，不是漏诊率——
    分母是"产生反馈的复核记录"，无患者结局/金标准。任何把此数字对外表述为
    "漏诊率"（含 15%→5% 目标口径）的用法都被 `disclaimer` 字段显式拒绝。
    详见 docs/技术文档/未打通项打通计划/04-治疗与患者结局-3项打通计划.md §05-3。
    """
    report = await assess_recent_corrections(db, window_days=window_days)
    return CorrectionRateProxyResponse(
        metric_kind="proxy_correction_rate",
        disclaimer=(
            "改判率≠漏诊率：本指标仅统计医生对 AI 结论的修正反馈"
            "（feedback_records.feedback_type='correction'），分母不含患者结局"
            "或病理/随访金标准，不能用于'漏诊率 15%→5%'等对外口径。"
            "真实漏诊率需最终诊断+金标准来源（见 05-3 S1 季度审计），"
            "当前未测量。"
        ),
        window_days=window_days or settings.correction_rate_window_days,
        insufficient_samples=report.insufficient_samples,
        min_samples=settings.correction_rate_min_samples,
        report=report.as_dict(),
    )


def _read_gpu_metrics(collector) -> dict:
    try:
        return {
            "memory_utilization": collector.get_gauge_value("gpu_memory_utilization"),
            "fragmentation_rate": collector.get_gauge_value("gpu_fragmentation_rate"),
            "temperature": collector.get_gauge_value("gpu_temperature"),
        }
    except Exception:
        logger.warning("obs_dashboard_gpu_metrics_read_failed", exc_info=True)
        return {}


def _read_hallucination_metrics(collector) -> dict:
    try:
        return {
            "severe_rate": collector.get_gauge_value("severe_hallucination_rate"),
            "overall_rate": collector.get_gauge_value("overall_hallucination_rate"),
            "high_risk_rate": collector.get_gauge_value("high_risk_hallucination_rate"),
        }
    except Exception:
        logger.warning("obs_dashboard_hallucination_metrics_read_failed", exc_info=True)
        return {}


def _read_latency_metrics(collector) -> dict:
    try:
        return {
            "nav_p99": collector.get_histogram_p99("nav_latency"),
            "assess_p99": collector.get_histogram_p99("assessment_latency"),
            "diag_p99": collector.get_histogram_p99("diagnosis_latency"),
            "multi_p99": collector.get_histogram_p99("multi_organ_latency"),
        }
    except Exception:
        logger.warning("obs_dashboard_latency_metrics_read_failed", exc_info=True)
        return {}


def _read_service_metrics(collector) -> dict:
    try:
        return {
            "health_rate": collector.get_gauge_value("service_health_rate"),
            "sg_healthy": collector.get_gauge_value("safety_gateway_health") == 1,
            "cache_hit_rate": collector.get_gauge_value("cache_hit_rate"),
            "retrieval_empty_rate": collector.get_gauge_value("retrieval_empty_rate"),
        }
    except Exception:
        logger.warning("obs_dashboard_service_metrics_read_failed", exc_info=True)
        return {}


def _read_cache_metrics(cache_mon) -> dict:
    try:
        metrics = cache_mon.get_all_metrics()
        return {
            "bloom_interception_rate": metrics.get("penetration", {}).get("bloom_filter_interception_rate"),
            "db_penetration_rate": metrics.get("penetration", {}).get("db_penetration_rate"),
            "mutex_success_rate": metrics.get("breakdown", {}).get("mutex_lock_success_rate"),
            "simultaneous_expiry_5min": metrics.get("avalanche", {}).get("simultaneous_expiry_5min"),
        }
    except Exception:
        logger.warning("obs_dashboard_cache_metrics_read_failed", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# A.109 / N1：熔断状态的**人工恢复入口**
#
# 缺口实测：`resume()` / `cancel_pending_suspension()` 早在代码里，但 app/ 内零调用、
# 没有任何 API/页面能触发；再叠加"pending 不晋级 + 重启从快照恢复"，一旦触发就永久
# `needs_review`，只能等快照 24h 过期。这里补三个受权限保护、必留审计的恢复动作。
#
# 权限：沿用本 router 的 `view_qc_report`（= 质控 + 系统管理员）。这与状态机的
# 原始设计一致（科室主任在确认窗口内判定是否为统计波动），不额外放宽。
# ---------------------------------------------------------------------------


class BreakerActionRequest(BaseModel):
    """熔断恢复动作的入参：**必须写原因**（无原因的恢复无法审计）。"""

    reason: str = Field(min_length=1, max_length=200, description="恢复原因（审计必填）")
    actor: str | None = Field(default=None, max_length=64, description="操作者标识（缺省取登录用户）")


def get_breaker() -> AISafetyCircuitBreaker:
    """可注入依赖：测试可 override，生产走单例。"""
    return AISafetyCircuitBreaker.get_instance()


async def _audit_breaker_action(audit_service, action: str, actor: str, detail: dict) -> None:
    await audit_service.log_action(
        action=action,
        actor=actor,
        target_type="safety_breaker",
        target_id="ai_safety_circuit_breaker",
        detail=detail,
    )


@router.post("/breaker/resume")
async def resume_breaker(
    payload: BreakerActionRequest,
    breaker: AISafetyCircuitBreaker = Depends(get_breaker),
    audit_service=Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    """把熔断器恢复到 ACTIVE（清空 pending/暂停病种/CUSUM 计数）。

    这是"人工确认属于统计波动"的动作：CUSUM 计数一并 `reset()`（否则下一次样本
    立刻再触发，恢复等于没恢复）。命中全部状态并写审计；重复调用是幂等的。
    """
    actor = payload.actor or get_actor_id(current_user)
    before = dict(breaker.suspension_info)
    breaker.resume(resumed_by=actor)
    after = dict(breaker.suspension_info)
    await _audit_breaker_action(
        audit_service,
        "ai_assist_resume_manual",
        actor,
        {
            "reason": payload.reason,
            "previous_state": before.get("state"),
            "previous_reason": before.get("reason"),
            "s_plus_reset_from": before.get("cusum_s_plus"),
            "current_state": after.get("state"),
        },
    )
    return {"ok": True, "previous": before, "current": after}


@router.post("/breaker/cancel-pending")
async def cancel_pending(
    payload: BreakerActionRequest,
    breaker: AISafetyCircuitBreaker = Depends(get_breaker),
    audit_service=Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    """在确认窗口内取消 pending（判定为统计波动），不触发暂停。

    与 `/breaker/resume` 的分工：本端点只对 `pending_confirmation` 生效（状态不对
    就 400，不静默成功），且**不清 CUSUM 计数**——窗口内赌一把之后若再触发，应当
    照常升级，而不是被这次取消掩盖。
    """
    actor = payload.actor or get_actor_id(current_user)
    before = dict(breaker.suspension_info)
    if not breaker.cancel_pending_suspension(canceller=actor):
        raise BadRequestException(
            error_message=f"当前状态不是待确认（{before.get('state')}），无法取消 pending"
        )
    after = dict(breaker.suspension_info)
    await _audit_breaker_action(
        audit_service,
        "ai_assist_cancel_pending",
        actor,
        {
            "reason": payload.reason,
            "previous_state": before.get("state"),
            "previous_reason": before.get("reason"),
            "current_state": after.get("state"),
        },
    )
    return {"ok": True, "previous": before, "current": after}


@router.post("/breaker/resume-disease")
async def resume_suspended_disease(
    payload: BreakerActionRequest,
    disease_type: str = Query(min_length=1, max_length=64),
    breaker: AISafetyCircuitBreaker = Depends(get_breaker),
    audit_service=Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    """解除单个病种的暂停（`resume_disease` 同样是零调用死方法）。"""
    actor = payload.actor or get_actor_id(current_user)
    if not breaker.resume_disease(disease_type):
        raise BadRequestException(error_message=f"病种未处于暂停状态：{disease_type}")
    await _audit_breaker_action(
        audit_service,
        "ai_assist_resume_disease",
        actor,
        {"reason": payload.reason, "disease_type": disease_type},
    )
    return {"ok": True, "disease_type": disease_type}


# ---------------------------------------------------------------------------
# D-04：撤回事件死信队列的**读取与重放**入口
#
# 缺陷（纠错台账 D-04 / wiring-audit P2-1）：`consent_withdrawal_consumer` 把
# 处理失败的事件 XADD 进死信流，但全仓**没有任何读取方** —— 运维看不到积压，
# 死信里的撤回事件永远不会被重试，病人的撤回意图静默丢失（合规面）。
#
# 权限沿用本 router 的 `view_qc_report`（质控 + 系统管理员），与熔断恢复一致：
# 重放会触发数据清理副作用，必须有人签字且写审计。
# ---------------------------------------------------------------------------


@router.get("/dead-letters")
async def list_dead_letters_api(
    limit: int = Query(default=20, ge=1, le=200, description="最多返回条数"),
):
    """列出撤回事件死信（最新在前，只读）。"""
    from app.security.dead_letter import dead_letter_depth, list_dead_letters

    rows = await list_dead_letters(limit=limit)
    return {
        "depth": await dead_letter_depth(),
        "returned": len(rows),
        "items": rows,
    }


class DeadLetterReplayRequest(BaseModel):
    """死信重放入参：``entry_id`` 必填、``reason`` 必填（无原因的重放无法审计）。"""

    entry_id: str = Field(min_length=1, max_length=64, description="死信 entry_id")
    reason: str = Field(min_length=1, max_length=200, description="重放原因（审计必填）")
    actor: str | None = Field(default=None, max_length=64, description="操作者标识（缺省取登录用户）")


@router.post("/dead-letters/replay")
async def replay_dead_letter_api(
    payload: DeadLetterReplayRequest,
    audit_service=Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    """把一条死信重新投回主处理流（留痕、不删原记录）。

    状态码语义：找不到 / 已重放 / 投递失败 → 400（不静默成功）；
    成功后写审计（`consent_withdrawal_dead_letter_replayed`）。
    """
    from fastapi import HTTPException

    from app.security.dead_letter import replay_dead_letter

    actor = payload.actor or get_actor_id(current_user)
    outcome = await replay_dead_letter(payload.entry_id, actor=actor)
    if not outcome["ok"]:
        raise HTTPException(status_code=400, detail=outcome["reason"])
    await audit_service.log_action(
        action="consent_withdrawal_dead_letter_replayed",
        actor=actor,
        target_type="consent_withdrawal_dead_letter",
        target_id=payload.entry_id,
        detail={
            "reason": payload.reason,
            "new_stream_entry": outcome["new_entry_id"],
        },
    )
    return {"ok": True, **outcome}


class KbCacheInvalidateRequest(BaseModel):
    """知识缓存失效入参（G8）：必须写原因，不允许无痕清缓存。"""

    reason: str = Field(min_length=1, max_length=200, description="失效原因（审计必填）")


@router.post("/kb-cache/invalidate")
async def invalidate_kb_cache(
    payload: KbCacheInvalidateRequest,
    audit_service=Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    """立即清空知识检索缓存（G8：此前只能等 L1 的 24h TTL）。

    与"改知识库版本号"是两件事：本端点**不动** kb_version，只清缓存。
    实现上写一个 Redis 更新戳，运行中的服务在下次检索时自行清 L2+L1 ——
    这样多实例部署时每个实例都会生效，不依赖本进程持有的缓存引用。
    """
    from app.config.redis import RedisPool

    actor = get_actor_id(current_user)
    stamp = f"{datetime.now(timezone.utc).isoformat()}|{payload.reason}|{actor}"
    try:
        client = await RedisPool.get_client()
        await client.set(RedisKey.KB_CACHE_INVALIDATION_STAMP, stamp)
    except Exception as exc:
        logger.warning("kb_cache_invalidate_failed", error=str(exc)[:160])
        raise HTTPException(
            status_code=503, detail="缓存服务暂不可用，请稍后重试"
        ) from exc

    await audit_service.log_action(
        action="kb_cache_invalidated",
        actor=actor,
        target_type="knowledge_cache",
        target_id="kb:cache",
        detail={"reason": payload.reason, "stamp": stamp},
    )
    return {"ok": True, "stamp": stamp, "note": "各实例将在下次检索时清空缓存"}
