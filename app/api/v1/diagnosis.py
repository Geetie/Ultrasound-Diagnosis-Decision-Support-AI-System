import json
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.routing import RoutingDecision
from app.api.v1.errors import (
    BadRequestException,
    DiagnosisQueueFullException,
    NotFoundException,
    SafetyGatewayUnavailableException,
)
from app.api.v1.utils import check_consent_status, get_actor_id, try_renew_session
from app.api.websocket.nav import manager as nav_manager
from app.config.database import get_db_session
from app.config.rate_limit import _get_user_id_from_request, check_rate_limit
from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey
from app.config.settings import settings
from app.config.validator import check_version_compatibility
from app.dependencies import get_audit_service, get_consent_repo, get_diagnosis_repo, get_session_repo
from app.models.schemas import (
    ClinicalAction,
    ConsentStatus,
    ConsistencyFlag,
    CrossModalityHint,
    DesensitizationStatus,
    DiagnosisEvidence,
    DiagnosisQualityControl,
    DiagnosisRequest,
    DiagnosisRequestResponse,
    DiagnosisResult,
    DifferentialDiagnosis,
    DoctorConfirmRequest,
    DoctorConfirmResponse,
    FINAL_DIAGNOSIS_SOURCES,
    FinalDiagnosisRequest,
    FinalDiagnosisResponse,
    FusionStatus,
    HallucinationSeverity,
    MalignancyRisk,
    Measurement,
    MultiOrganDiagnosisRequest,
    MultiOrganDiagnosisResponse,
    PatientExplanation,
    ResearchMetrics,
    ResponseTier,
    RetryStrategy,
    RoutingConfirmationRequest,
    RoutingConfirmationResponse,
)
from app.security.auth import PermissionChecker, get_current_active_user
from app.services.admission_control import check_diagnosis_admission
from app.services.diagnosis_pipeline import get_routing_router
from app.services.diagnosis_worker import enqueue_diagnosis
from app.services.multi_organ_orchestrator import (
    SessionNotFoundError,
    run_multi_organ_diagnosis,
)
from app.services.safety_gateway_client import safety_gateway_client
from app.services.sg_output_data import build_sg_output_data, extract_findings_text
from app.services.similar_cases import response_block as similar_cases_response_block

router = APIRouter(tags=["diagnosis"])
logger = structlog.get_logger()

IDEMPOTENCY_TTL_SECONDS = 600
# 结果读取的 processing 超时阈值（秒）。
#
# P1-13 修复（2026-10-04）：原为硬编码 30 秒，而真实诊断管线要 90 秒以上
# （含 dispatcher + fusion 的 LLM 串行链；压测记录单例稳态 9.5~9.7s 是 LLM
# 已热的情形，冷启动还要更久）。30 秒会把**正常在跑**的诊断误报成 timeout，
# 前端据此停止轮询 → 用户看到"超时失败"而后台其实快要出结果了。
#
# 现从配置读取（不同部署规模差异大：LLM 换小模型可调小、换大模型必须调大），
# 默认 180 秒。这个值**只影响读路径展示**，不终止后台管线——真超时由
# diagnosis_pipeline 自己的预算控制。
RESULT_TIMEOUT_SECONDS = int(
    getattr(settings, "result_read_timeout_seconds", 180)
)


def _hallucination_severity_of(
    fusion_payload: object,
) -> "HallucinationSeverity | None":
    """从落库的 ``findings.fusion_result`` 取幻觉严重度（A.88）。

    ## 为什么必须区分「未评估」与「评估过且干净」

    ``HallucinationSeverity`` 只有 ``severe/moderate/mild`` 三个值 —— **没有
    "clean/none"**。所以 `None` 是**双义**的：既可能是"没跑检测"，也可能是
    "跑了、没检出问题"。把两者混成一个 `None` 就是又一次"把没检查过写成通过"
    （与 ``quality_control`` 里被批准的保守 ``False`` 同族）。

    因此本函数只回答"**该字段该填什么**"，而"有没有跑过"由同级的
    ``hallucination_checked`` 显式承载（管线写入，前端/审计读它）：
      * ``hallucination_checked`` 不是 True → 未评估 → 返回 None；
      * 已评估且给出可识别取值 → 归一为枚举；
      * 已评估但干净 / 取值不可识别 → None（**不可识别时不猜**，并把原始值
        记进日志，避免"写错了但也看不出来"）。
    """
    if not isinstance(fusion_payload, dict) or not fusion_payload:
        return None
    if fusion_payload.get("hallucination_checked") is not True:
        return None

    raw = fusion_payload.get("hallucination_severity")
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    try:
        return HallucinationSeverity(text)
    except ValueError:
        # 未识别取值：不猜（猜成 mild 等于凭空减轻一条质检结论），只留痕。
        logger.warning("hallucination_severity_unrecognized", raw=str(raw)[:40])
        return None


def _generate_request_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"req_{ts}_{uuid.uuid4().hex[:8]}"


def clinical_attribute_fields(diagnosis_payload: dict | None) -> dict:
    """从 findings 的 ``clinical_attributes`` 映射出对外契约字段（M1~M12）。

    管线把专科响应里的结构化临床属性写进 findings 的 ``clinical_attributes``
    子字典，本函数是把它转成**类型化契约字段**的唯一出口（写库结构不进 API
    契约，避免 DB 结构变化直接泄漏到前端）。

    容错策略：逐字段独立解析，单个字段结构异常不影响其它字段——诊断结果页
    能展示多少就展示多少，不能因为一个测量值格式不对就让整个结果接口 500
    （这是医生正在看的页面）。
    """
    clinical = (diagnosis_payload or {}).get("clinical_attributes")
    if not isinstance(clinical, dict):
        # 始终返回完整键集（值为默认空），调用方可以直接 **展开 而不必
        # 关心"有没有 clinical_attributes"
        clinical = {}

    def _one(key: str, model):
        value = clinical.get(key)
        if not value:
            return None
        try:
            return model.model_validate(value)
        except Exception as exc:
            logger.warning(
                "clinical_attribute_parse_failed",
                field=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    def _many(key: str, model) -> list:
        values = clinical.get(key)
        if not isinstance(values, list):
            return []
        parsed = []
        for item in values:
            try:
                parsed.append(model.model_validate(item))
            except Exception as exc:
                logger.warning(
                    "clinical_attribute_parse_failed",
                    field=key,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        return parsed

    return {
        "measurement": _one("measurement", Measurement),
        # M5：恶性概率必须与置信度分开展示（语义不同）
        "malignancy_risk": _one("malignancy_risk", MalignancyRisk),
        "patient_explanation": _one("patient_explanation", PatientExplanation),
        "research_metrics": _one("research_metrics", ResearchMetrics),
        "differential_diagnoses": _many("differential_diagnoses", DifferentialDiagnosis),
        "clinical_actions": _many("clinical_actions", ClinicalAction),
        "cross_modality_hints": _many("cross_modality_hints", CrossModalityHint),
        "posterior_acoustic": clinical.get("posterior_acoustic") or None,
    }


async def _push_diagnosis_result(result, request_id: str, db: AsyncSession):
    try:
        await nav_manager.send_diagnosis_result(result.session_id, request_id)
        result.result_pushed = True
        await db.flush()
    except Exception as e:
        logger.warning("Failed to push diagnosis result via WebSocket", error=str(e))



async def _mark_request_failed(
    diagnosis_repo,
    *,
    request_id: str,
    reason: str,
    message: str,
) -> None:
    """把未能受理的判读请求标成 ``failed``（A.53：入队失败不再留僵尸 processing 行）。

    为什么必须落库：请求行在建会话/提交阶段已经 commit（worker 需要它可见），
    不标失败的话它会永远停在 processing，医生轮询到超时也拿不到结论，事后排查
    还看不出"其实根本没入队"。这里用**既有仓储方法**做状态迁移，并把可读原因写进
    findings，失败不抛出（标记失败是尽力而为，不能盖掉对外的 503 语义）。
    """
    try:
        await diagnosis_repo.update_result(
            request_id=request_id,
            status="failed",
            findings={
                "status": "failed",
                "error": reason,
                "error_message": message,
                "reason": "未受理：判读未能入队（系统繁忙）",
            },
        )
        await diagnosis_repo.db.commit()
        logger.info("diagnosis_request_marked_failed", request_id=request_id, reason=reason)
    except Exception as exc:
        logger.error(
            "diagnosis_request_mark_failed_error",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )

@router.post("/request", response_model=DiagnosisRequestResponse)
async def submit_diagnosis_request(
    request: DiagnosisRequest,
    fastapi_request: Request,
    x_priority: int = Header(default=1, description="优先级: 0=急诊, 1=常规, 2=教学"),
    diagnosis_repo=Depends(get_diagnosis_repo),
    consent_repo=Depends(get_consent_repo),
    session_repo=Depends(get_session_repo),
    audit_service=Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
    # A.88 权限接线②：``submit_diagnosis`` 权限位（此前 PERMISSIONS 零生产调用）。
    # 只插入依赖参数，下面的函数体一行未改。
    _perm: dict = Depends(PermissionChecker("submit_diagnosis")),
):
    client_ip = fastapi_request.client.host if fastapi_request.client else "unknown"
    user_id = _get_user_id_from_request(fastapi_request)
    is_emergency = x_priority == 0

    skip_rate_limit = request.retry

    rate_limit_exc = await check_rate_limit(
        path="/api/v1/diagnosis/request",
        client_ip=client_ip,
        user_id=user_id,
        is_emergency=is_emergency,
        skip=skip_rate_limit,
        dedup_key=f"{request.session_id}",
    )
    if rate_limit_exc:
        raise rate_limit_exc

    if request.idempotency_key:
        cache_key = RedisKey.diagnosis_idempotency(request.idempotency_key)
        idempotency_result = await RedisPool.get_with_error(cache_key)
        if idempotency_result.error:
            logger.warning("idempotency_check_redis_error", key=cache_key)
        elif idempotency_result.value:
            raw = idempotency_result.decode_value()
            cached_data = json.loads(raw)
            cached_request_id = cached_data.get("request_id")
            if cached_request_id:
                result_cache_key = RedisKey.diagnosis_result(cached_request_id)
                result_cached = await RedisPool.get_with_error(result_cache_key)
                if result_cached.error:
                    logger.warning("idempotency_result_cache_redis_error", key=result_cache_key)
                elif result_cached.value:
                    result_data = json.loads(result_cached.decode_value())
                    if result_data.get("status") == "completed":
                        from fastapi.responses import JSONResponse
                        return JSONResponse(
                            status_code=200,
                            content=result_data,
                            headers={"X-Idempotent-Replay": "true"},
                        )
                    return DiagnosisRequestResponse(**result_data) if "session_id" in result_data else DiagnosisRequestResponse(**cached_data)
            return DiagnosisRequestResponse(**cached_data)

    session = await session_repo.get_session(request.session_id)
    if not session:
        raise NotFoundException(error_message=f"检查会话不存在：{request.session_id}")

    consent_record = await consent_repo.get_latest_consent(session.patient_id)
    check_consent_status(consent_record, session.patient_id)

    # === A.78 病人隔离（提交侧）===
    # 原实现把 `request.image_ids` **原样落库**、不校验归属：调用方可以传别的 session/病人的影像 ID。
    # 管线侧虽然会用 `filter_existing_in_session` 兜住（读图不会跨病人），但：
    #   ① 库里会留下跨 session 的脏 image_ids；② 请求会静默退化成 `image_not_indexed`；
    #   ③ 一旦影像向量腿建好，历史脏 ID 会被继续沿用。
    # 所以在**入口**就显式拒绝（400），而不是让它一路静默降级。
    if request.image_ids:
        from app.repositories.dicom_image_repository import DicomImageRepository

        missing = await DicomImageRepository(diagnosis_repo.db).missing_from_session(
            request.session_id, list(request.image_ids)
        )
        if missing:
            raise BadRequestException(
                error_message=(
                    f"以下影像不属于本次检查会话：{', '.join(missing[:3])}"
                    f"{' 等' if len(missing) > 3 else ''}；"
                    "请确认影像已上传到同一会话后再提交"
                ),
            )

    # === 准入控制（第二梯队② 生产容量验收，2026-10-12）===
    # 放在**建行之前**：队列过深时立刻 503 + Retry-After，绝不落一条永远等不到
    # worker 的 processing 行（那种"静默排队"既骗医生又污染数据）。
    # 探测不到深度（Redis 抖动）时放行 —— 入队侧还有自己的长度上限兜底。
    queue_depth = await check_diagnosis_admission()

    request_id = _generate_request_id()

    # 幂等键并发占位（A.50.7 E12 探针实测：check-then-set 在并发窗口下两个
    # 请求都 miss → 生成两个 request_id，幂等失效）。先在自己名下生成 id，
    # 用 **SET NX 原子占位**；占位失败说明并发对手已占 —— 读对手的
    # request_id 返回（复用其结果路径），自己不再落库/入队。
    if request.idempotency_key:
        idem_key = RedisKey.diagnosis_idempotency(request.idempotency_key)
        placeholder = json.dumps({"request_id": request_id, "session_id": request.session_id})
        won = await RedisPool.set_nx(idem_key, IDEMPOTENCY_TTL_SECONDS, placeholder)
        if not won:
            winner = await RedisPool.get_with_error(idem_key)
            winner_id = ""
            if not winner.error and winner.value:
                winner_id = json.loads(winner.decode_value()).get("request_id", "")
            if winner_id:
                logger.info("idempotency_concurrent_reuse", key=request.idempotency_key,
                            winner=winner_id, loser=request_id)
                return DiagnosisRequestResponse(
                    request_id=winner_id,
                    session_id=request.session_id,
                    status="processing",
                    estimated_time_ms=3000 if request.response_tier == ResponseTier.FULL else 2000,
                    response_tier=request.response_tier,
                )
            # NX 失败但读不到值（极端：对手刚过期）——继续走正常生成路径

    # request_id 必须由端点生成并**贯穿全链**（落库 / 入队 / 响应 / 结果查询）。
    # 若让 `create_request` 自己再生成一个，库里存的与队列里传的就是两个不同 id
    # → worker 永远 `pipeline_request_not_found`、请求永久停在 processing（真机实测）。
    await diagnosis_repo.create_request(
        session_id=request.session_id,
        organ=request.target_organ,
        response_tier=request.response_tier,
        patient_context=request.patient_context.model_dump() if request.patient_context else None,
        image_ids=request.image_ids,
        clinical_text=request.clinical_text,
        request_id=request_id,
    )

    # 必须先**提交**再入队（2026-09-28 真机链路冒烟暴露的阻断级缺陷）：
    # `create_request` 只 `flush()` 不提交，而 worker 一旦 BLPOP 到 request_id，
    # 是用**另一个会话**去读库 —— 未提交的行对它不可见，于是报
    #   pipeline_request_not_found → 请求永久停在 `status=processing`、
    #   结果接口一直 404，且**没有任何重试**能把它救回来。
    # 顺序不能颠倒：先提交（行可见）→ 再入队（worker 才可能读到）。
    await diagnosis_repo.db.commit()

    # T-GA-INT-01: 把 request_id 入队到后台 worker 池。
    # **入队失败必须显式失败**（A.53 修正）：此前只记 warning 就返回 200 —— 请求行
    # 已在库里（status=processing）却没有任何 worker 会处理它，医生轮询到超时也等不到
    # 结论，而且这条僵尸行还会一直留在库里。现在：标记该行为 failed（带可读原因），
    # 并抛 503 + Retry-After，让前端明确重试而不是空等。
    try:
        enqueued = await enqueue_diagnosis(request_id)
        if not enqueued:
            logger.error(
                "diagnosis_enqueue_failed_endpoint",
                request_id=request_id,
                queue_depth=queue_depth,
            )
            await _mark_request_failed(
                diagnosis_repo,
                request_id=request_id,
                reason="enqueue_failed_queue_full",
                message="系统队列已满，本次判读未能入队，请稍后重试。",
            )
            raise DiagnosisQueueFullException(
                error_message="系统队列已满，本次判读未能入队，请稍后重试（未产生结论，可安全重发）。",
                headers={"Retry-After": str(settings.diagnosis_admission_retry_after_s)},
            )
    except DiagnosisQueueFullException:
        raise
    except Exception as exc:
        logger.error(
            "diagnosis_enqueue_exception_endpoint",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        await _mark_request_failed(
            diagnosis_repo,
            request_id=request_id,
            reason="enqueue_exception",
            message="入队过程出错，本次判读未能受理，请稍后重试。",
        )
        raise DiagnosisQueueFullException(
            error_message="系统暂时无法受理判读（入队异常），请稍后重试。",
            headers={"Retry-After": str(settings.diagnosis_admission_retry_after_s)},
        ) from exc

    if request.retry and not request.retry_strategy:
        request.retry_strategy = RetryStrategy.FULL_RETRIEVAL

    if request.retry:
        retry_flag_key = RedisKey.retry_skip_self_rag(request_id)
        try:
            await RedisPool.setex(
                retry_flag_key,
                IDEMPOTENCY_TTL_SECONDS,
                json.dumps({
                    "skip_self_rag": True,
                    "retry_strategy": request.retry_strategy.value if request.retry_strategy else None,
                }),
            )
        except Exception as e:
            logger.warning("retry_flag_set_failed", request_id=request_id, error=str(e))

    estimated_time_ms = 3000 if request.response_tier == ResponseTier.FULL else 2000

    response_data = DiagnosisRequestResponse(
        request_id=request_id,
        session_id=request.session_id,
        status="processing",
        estimated_time_ms=estimated_time_ms,
        response_tier=request.response_tier,
    )

    if request.idempotency_key:
        cache_key = RedisKey.diagnosis_idempotency(request.idempotency_key)
        await RedisPool.setex(
            cache_key,
            IDEMPOTENCY_TTL_SECONDS,
            json.dumps(response_data.model_dump())
        )

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="diagnosis_request",
        actor=actor_id,
        session_id=request.session_id,
        target_type="diagnosis",
        target_id=request_id,
        detail={
            "organ": request.target_organ,
            "response_tier": request.response_tier.value,
            "priority": x_priority,
            "retry": request.retry,
            "retry_strategy": request.retry_strategy.value if request.retry_strategy else None,
        },
    )

    await try_renew_session(session_repo, request.session_id)

    return response_data


RESULT_CACHE_TTL_SECONDS = 600


@router.post(
    "/{request_id}/confirm-upgrade", response_model=RoutingConfirmationResponse
)
async def confirm_routing_upgrade(
    request_id: str,
    payload: RoutingConfirmationRequest,
    diagnosis_repo=Depends(get_diagnosis_repo),
    audit_service=Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    """T-GB-AGT-02：医生对"是否升级到全量检索档位"的确认回写。

    铁律「升级决策权交医生」的落地：

    - 只有"多特征组合推理"的判定会带 ``requires_confirmation=True``；
    - 医生确认 → 请求档位改为 ``full`` 并重新入队（管线的路由判定会
      直接给出全量路径，``reasons`` 含 ``doctor_requested_full``）；
    - 医生拒绝 → 保持原档位，仅记录确认结果（**不可降级**：跨器官 /
      置信度不足两类情形从一开始就走全量，不产生待确认项）。
    """
    result = await diagnosis_repo.get_result(request_id)
    if not result:
        raise NotFoundException(error_message=f"判读结果不存在：{request_id}")

    findings = dict(result.findings) if isinstance(result.findings, dict) else {}
    decision = RoutingDecision.from_dict(findings.get("routing"))
    if decision is None:
        raise BadRequestException(
            error_message="诊断结果中不存在可确认的路由判定（findings.routing 缺失或非法）"
        )
    if not decision.pending_upgrade:
        raise BadRequestException(
            error_message="当前路由判定不存在待确认的档位升级"
        )

    router = get_routing_router()
    final = router.apply_confirmation(decision, payload.confirmed)

    actor_id = get_actor_id(current_user)
    routing_record = final.as_dict()
    routing_record["confirmation"] = {
        "confirmed": payload.confirmed,
        "confirmed_by": actor_id,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "reason": payload.reason,
    }

    previous_status = result.status
    rerun_enqueued = False

    if payload.confirmed:
        # 用全量档位重跑：改 tier → 管线按 doctor_requested_full 走全量路径。
        await diagnosis_repo.update_tier(request_id, ResponseTier.FULL.value)
        merged_findings = {**findings, "routing": routing_record}
        await diagnosis_repo.update_result(
            request_id=request_id,
            status="processing",
            confidence=result.confidence,
            findings=merged_findings,
            recommendations=result.recommendations,
            knowledge_ids=result.knowledge_ids,
        )
        enqueued = await enqueue_diagnosis(request_id)
        if enqueued:
            rerun_enqueued = True
        else:
            # 入队失败（Redis 与进程内队列均不可用）：回滚为原状态，避免
            # 结果永久停在 processing 而让前端一直轮询到 timeout。
            logger.warning(
                "routing_upgrade_rerun_enqueue_failed",
                request_id=request_id,
            )
            await diagnosis_repo.update_result(
                request_id=request_id,
                status=previous_status,
                confidence=result.confidence,
                findings=merged_findings,
                recommendations=result.recommendations,
                knowledge_ids=result.knowledge_ids,
            )
    else:
        await diagnosis_repo.update_findings(
            request_id, {**findings, "routing": routing_record}
        )

    try:
        await diagnosis_repo.db.commit()
    except Exception as exc:
        logger.warning("routing_confirmation_commit_failed", error=str(exc))

    await audit_service.log_action(
        action="routing_upgrade_confirmation",
        actor=actor_id,
        session_id=getattr(result, "session_id", None),
        target_type="diagnosis",
        target_id=request_id,
        detail={
            "confirmed": payload.confirmed,
            "effective_tier": final.effective_tier.value,
            "requested_tier": final.requested_tier.value,
            "reasons": [r.value for r in final.reasons],
            "rerun_enqueued": rerun_enqueued,
            "reason": payload.reason,
        },
    )

    return RoutingConfirmationResponse(
        request_id=request_id,
        confirmed=payload.confirmed,
        effective_tier=final.effective_tier,
        requires_confirmation=final.requires_confirmation,
        rerun_enqueued=rerun_enqueued,
        routing=routing_record,
    )


@router.post(
    "/{request_id}/doctor-confirm", response_model=DoctorConfirmResponse
)
async def doctor_confirm_result(
    request_id: str,
    payload: DoctorConfirmRequest,
    diagnosis_repo=Depends(get_diagnosis_repo),
    audit_service=Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    """回写医生「我看过这条结论了」。

    设计依据 `前端设计/17-医生侧展示契约.md:137-141`：

        | 5 | 语音确认／修正 | 不可省（防 automation bias） |
        | 8 | 签发           | 不可省（法律责任节点）       |

    **确认与签发是两件事，本端点只做确认**：
    - 不签任何名、不生成报告、不改 status / confidence；
    - 唯一的副作用是记下「谁、什么时候看过」；
    - 前端据此撤掉结论卡上的「AI 层标记」（同文档 `:164` 要求在确认后消失）。

    在此之前，工作区的「确认结论」按钮只执行 `setAcknowledged(true)`，
    全仓无人读该状态，等于医生点了没有任何痕迹。
    """
    result = await diagnosis_repo.get_result(request_id)
    if not result:
        raise NotFoundException(error_message=f"判读结果不存在：{request_id}")

    actor_id = get_actor_id(current_user)
    confirmed = await diagnosis_repo.mark_doctor_confirmed(
        request_id=request_id, confirmed_by=actor_id
    )
    if confirmed is None or confirmed.doctor_confirmed_at is None:
        raise BadRequestException(error_message="确认回写失败，请重试")

    # 写审计：确认是"医生参与过"的证据，出事时要能倒查
    await audit_service.log_action(
        action="doctor_confirm_result",
        actor=actor_id,
        session_id=getattr(confirmed, "session_id", None),
        target_type="diagnosis",
        target_id=request_id,
        detail={
            "doctor_confirmed_at": confirmed.doctor_confirmed_at.isoformat(),
            "doctor_confirmed_by": actor_id,
        },
    )

    return DoctorConfirmResponse(
        request_id=request_id,
        doctor_confirmed_at=confirmed.doctor_confirmed_at,
        doctor_confirmed_by=actor_id,
    )


@router.patch(
    "/{request_id}/final-diagnosis", response_model=FinalDiagnosisResponse
)
async def set_final_diagnosis(
    request_id: str,
    payload: FinalDiagnosisRequest,
    diagnosis_repo=Depends(get_diagnosis_repo),
    audit_service=Depends(get_audit_service),
    _perm: dict = Depends(PermissionChecker("write_final_diagnosis")),
    current_user: dict | None = Depends(get_current_active_user),
):
    """回填最终诊断与金标准来源（05-3 S1，季度回顾审计的机器写入口）。

    对象级授权：仅 ``write_final_diagnosis`` 权限位（QUALITY_CONTROL / 系统管理员）
    可写——写"金标准"的人不该是出初始结论的同角色（审计独立性）。

    **单一"最终诊断"不定罪**：必须与 ``final_diagnosis_source``（pathology /
    followup / expert_consensus）成对写入；缺一不可。漏诊率的分母由此而来
    （``scripts/audit/p76_missed_rate_audit.py``），分母为 0 时该脚本输出
    ``not_measured_no_final_diagnosis`` 绝不输出 0。

    副作用边界：只写 4 个审计列，不动判读产物。
    """
    if payload.final_diagnosis_source not in FINAL_DIAGNOSIS_SOURCES:
        raise BadRequestException(
            error_message=(
                "final_diagnosis_source 必须是 "
                f"{sorted(FINAL_DIAGNOSIS_SOURCES)} 之一"
            )
        )

    result = await diagnosis_repo.get_result(request_id)
    if not result:
        raise NotFoundException(error_message=f"判读结果不存在：{request_id}")

    actor_id = get_actor_id(current_user)
    written = await diagnosis_repo.mark_final_diagnosis(
        request_id=request_id,
        final_diagnosis=payload.final_diagnosis,
        final_diagnosis_source=payload.final_diagnosis_source,
        written_by=actor_id,
    )
    if written is None or written.final_diagnosis_at is None:
        raise BadRequestException(error_message="最终诊断回写失败，请重试")

    await audit_service.log_action(
        action="set_final_diagnosis",
        actor=actor_id,
        session_id=getattr(written, "session_id", None),
        target_type="diagnosis",
        target_id=request_id,
        detail={
            "final_diagnosis_source": payload.final_diagnosis_source,
            "final_diagnosis_at": written.final_diagnosis_at.isoformat(),
        },
    )

    return FinalDiagnosisResponse(
        request_id=request_id,
        final_diagnosis=written.final_diagnosis,
        final_diagnosis_source=written.final_diagnosis_source,
        final_diagnosis_at=written.final_diagnosis_at,
        final_diagnosis_by=actor_id,
    )


@router.post("/multi-organ", response_model=MultiOrganDiagnosisResponse)
async def submit_multi_organ_diagnosis(
    request: MultiOrganDiagnosisRequest,
    fastapi_request: Request,
    session_repo=Depends(get_session_repo),
    consent_repo=Depends(get_consent_repo),
    audit_service=Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
    # A.88 权限接线②：多器官会话同样是"提交判读"，用同一权限位。
    _perm: dict = Depends(PermissionChecker("submit_diagnosis")),
):
    """T-GB-AGT-01：会话级多器官联合诊断（三器官串行 + 部分完成）。

    与单器官 ``POST /request`` 的差异：
    - 一次调用按规格顺序串行处理会话的全部目标器官；
    - 单器官超时/失败不阻断其余器官，响应中返回各器官状态与
      ``partial`` 标记；
    - 会话 ``organ_statuses`` 与状态同步更新，前端可据此渲染进度
      （WebSocket 侧推送 ``organ_switching``）。
    """
    session = await session_repo.get_session(request.session_id)
    if not session:
        raise NotFoundException(error_message=f"检查会话不存在：{request.session_id}")

    consent_record = await consent_repo.get_latest_consent(session.patient_id)
    check_consent_status(consent_record, session.patient_id)

    # 多器官一次要占住一个 worker 跑完整串行链 → 同样走准入（占用更久，更该早拒）
    await check_diagnosis_admission()

    try:
        outcome = await run_multi_organ_diagnosis(
            session_id=request.session_id,
            organs=request.organs,
            per_organ_timeout_s=request.per_organ_timeout_seconds,
            total_timeout_s=request.total_timeout_seconds,
            response_tier=request.response_tier,
            # A.88：临床事实随请求上行（此前该请求体没有这些字段 → 只能从会话
            # 已有请求继承；若本会话从未发过单器官请求，医生填的临床信息与
            # 勾选的禁忌条件就进不了安全网关）。用 model_dump 而不是直接传
            # Pydantic 对象：orchestrator 与 create_request 都按 dict 处理。
            clinical_text=request.clinical_text,
            patient_context=(
                request.patient_context.model_dump()
                if request.patient_context is not None
                else None
            ),
            image_ids=request.image_ids,
        )
    except SessionNotFoundError as exc:
        raise NotFoundException(error_message=str(exc)) from exc

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="diagnosis_multi_organ",
        actor=actor_id,
        session_id=request.session_id,
        target_type="diagnosis",
        target_id=request.session_id,
        detail={
            "organs": outcome.organs,
            "session_status": outcome.session_status,
            "partial": outcome.partial,
            "total_ms": round(outcome.total_ms, 2),
            "organ_statuses": outcome.organ_statuses,
        },
    )

    await try_renew_session(session_repo, request.session_id)

    return MultiOrganDiagnosisResponse(
        session_id=outcome.session_id,
        session_status=outcome.session_status,
        organs=outcome.organs,
        organ_statuses=outcome.organ_statuses,
        organ_results=[r.as_dict() for r in outcome.organ_results],
        completed_organs=outcome.completed_organs,
        failed_organs=outcome.failed_organs,
        skipped_organs=outcome.skipped_organs,
        needs_review_organs=outcome.needs_review_organs,
        partial=outcome.partial,
        total_ms=outcome.total_ms,
        # A.88：跨器官融合结果（冲突检测 + 跨器官一致性）随响应返回；
        # 空 dict = 未执行/无融合引擎，绝不用"无冲突"冒充"没跑"。
        cross_organ=outcome.cross_organ,
    )


@router.get("/result/{request_id}", response_model=DiagnosisResult)
async def get_diagnosis_result(
    request_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    diagnosis_repo=Depends(get_diagnosis_repo),
    consent_repo=Depends(get_consent_repo),
    session_repo=Depends(get_session_repo),
    current_user: dict | None = Depends(get_current_active_user),
    # A.88 权限接线②：``view_results`` 权限位（质控管理员/急诊医生也能看）。
    _perm: dict = Depends(PermissionChecker("view_results")),
):
    cache_key = RedisKey.diagnosis_result(request_id)
    cached = await RedisPool.get_with_error(cache_key)
    if cached.error:
        logger.warning("result_cache_redis_error", key=cache_key)
    elif cached.value:
        raw = cached.decode_value()
        return DiagnosisResult.model_validate(json.loads(raw))

    result = await diagnosis_repo.get_result(request_id)
    if not result:
        raise NotFoundException(error_message=f"判读结果不存在：{request_id}")

    # 撤回消费链的 cancelled 终态必须**可读**：管线（diagnosis_pipeline.py 的
    # CancelledError 处理）特意 asyncio.shield 写回 status=cancelled，供医生/审计
    # 观测"请求因撤回被取消"。若让它先过下面的同意门，撤回患者的每个结果查询
    # 都抛 E1010 —— 终态反而不可观测（A.49 真机探针实测：API observed=None，
    # DB status=cancelled）。提前返回，且**不携带任何临床内容**（findings 占位）。
    if result.status == "cancelled":
        return DiagnosisResult(
            request_id=request_id,
            status="cancelled",
            # A.88 A3：cancelled = **一致性校验根本没跑**（请求在融合/校验之前
            # 就被撤回取消，管线直接写终态）。原写 CONSISTENT 是把"未评估"说成
            # "一致" —— 与 `quality_control` 里被批准的保守 False 是同一族问题的
            # 两个方向：那里"未评估→不得声称通过"，这里"未评估→不得声称一致"。
            # schema 允许 `ConsistencyFlag | None`，故给 None（不编造）。
            consistency_flag=None,
            desensitization_status=DesensitizationStatus.FULL,
            consent_status_at_output=ConsentStatus.WITHDRAWN,
            # A.88 A3：同理，本分支没有 findings.fusion_result（连 findings 都
            # 没有，payload 是占位），融合层未产出 → BYPASSED，而非 NORMAL。
            fusion_status=FusionStatus.BYPASSED,
            diagnosis={
                "organ": result.organ,
                "findings": "Diagnosis cancelled: patient consent withdrawn",
                "classification": "N/A",
                "recommendation": "N/A",
                "pending": True,
            },
            nodule_segmentation={"available": False, "reason": "cancelled"},
            similar_cases=similar_cases_response_block({"status": "cancelled"}),
            evidence=DiagnosisEvidence(knowledge_ids=None, content=None),
            quality_control=DiagnosisQualityControl(
                confidence_score=None,
                self_rag_passed=False,
                consistency_check_passed=False,
            ),
            requires_manual_review=True,
            review_reason="consent_withdrawn_cancelled",
            ai_diagnosis_failed=False,
            # A.88 A3：`hallucination_severity` **无生产者**（仓库内没有任何
            # 模块产出幻觉严重度），任何取值都是编造；保持 None 直到真有生产者。
            hallucination_severity=None,
            version_compatibility=check_version_compatibility(
                settings.resolved_model_version, settings.rag_kb_version
            ),
            processing_time_ms=0,
            model_version=settings.resolved_model_version,
            # A.52：知识库版本用生产真值（不写 Phase 0A 占位常量）
            knowledge_base_version=settings.rag_kb_version,
        )

    session = await session_repo.get_session(result.session_id)
    patient_id = session.patient_id if session else "unknown"
    consent_record = await consent_repo.get_latest_consent(patient_id)
    consent_status = check_consent_status(consent_record, patient_id)

    # 读路径的载荷必须与写路径（app/services/diagnosis_pipeline.py）**同一个构造函数**。
    # 2026-09-29 真机暴露：这里原先直接把 worker 落库的整包 findings
    # （organ/fusion_result/sg_result/timings_ms）当 output_data，且**不带患者条件** ——
    # 于是同一份诊断在写处 pass、在读处被判"检测到禁忌"，
    # 响应里的 `requires_manual_review` 恒为真、系统永远给不出自动结论。
    _stored = result.findings if isinstance(result.findings, dict) else {}
    sg_result = await safety_gateway_client.check_output(
        session_id=result.session_id,
        patient_id=patient_id,
        output_data=build_sg_output_data(
            organ=result.organ,
            findings=extract_findings_text(_stored.get("fusion_result")),
            patient_context=getattr(result, "patient_context", None),
        ),
        consent_status_at_input=consent_status.value,
    )

    requires_manual_review = False
    review_reason = None

    if not sg_result.get("passed", sg_result.get("allowed", False)):
        if sg_result.get("service_unavailable", False):
            raise SafetyGatewayUnavailableException()
        desensitization_status = DesensitizationStatus.DEGRADED
        consent_status_at_output = ConsentStatus.DENIED
        requires_manual_review = True
        review_reason = sg_result.get("message", "安全校验未通过")
    else:
        consent_status_at_output = ConsentStatus.GRANTED
        sg_consent = sg_result.get("consent_status_at_output", "granted")
        if sg_consent == "withdrawn":
            consent_status_at_output = ConsentStatus.WITHDRAWN
        elif sg_consent in ("unknown_degraded", "unknown_blocked"):
            consent_status_at_output = ConsentStatus.DENIED

        desensitization_status = DesensitizationStatus.FULL
        if sg_result.get("degraded_mode"):
            desensitization_status = DesensitizationStatus.DEGRADED

    result_status = result.status
    if result_status == "processing" and result.created_at:
        elapsed = (datetime.now(timezone.utc) - result.created_at).total_seconds()
        if elapsed > RESULT_TIMEOUT_SECONDS:
            result_status = "timeout"

    # 后台 worker（app/services/diagnosis_pipeline.py）写回的 findings 即
    # 真实诊断结论，其结构为 organ / fusion_result / sg_result / timings_ms。
    # 只有 worker 尚未写入（status=processing/timeout）时才给占位 payload。
    diagnosis_payload = result.findings if isinstance(result.findings, dict) and result.findings else None
    if diagnosis_payload:
        timings = diagnosis_payload.get("timings_ms") or {}
        # A.91：**不许把"没测到"写成 2500ms**（假数据的一个典型来源）。
        # 原写法 `int(...) or settings.phase0a_mock_processing_time_ms` 会在
        # findings 有内容、但 `timings_ms` 缺失/为 0 时静默返回**写死的 2500ms**。
        # 后果：接口上的 `processing_time_ms` 看起来是真实耗时（读者会拿它算
        # P50/P99 甚至当门控证据），实际是个常量 —— 这正是"看起来像真数据"。
        # 现在：有真值用真值；真值缺失 → 0（表示"未测到"），并在日志留痕。
        _raw_total = timings.get("pipeline.total_ms")
        try:
            processing_time_ms = int(float(_raw_total)) if _raw_total else 0
        except (TypeError, ValueError):
            logger.warning(
                "result_processing_time_unparseable",
                request_id=request_id,
                raw=str(_raw_total)[:40],
            )
            processing_time_ms = 0
        if processing_time_ms == 0:
            logger.info(
                "result_processing_time_unavailable",
                request_id=request_id,
                has_timings=bool(timings),
                hint="findings 有内容但缺 pipeline.total_ms → 不编造耗时",
            )
        model_version = diagnosis_payload.get("model_version") or settings.resolved_model_version
        # A.52 溯源：生成模型标识（哪个模型出的结论）；管线已写回，回退取运行期真值
        llm_model = diagnosis_payload.get("llm_model") or settings.resolved_llm_model
        # A.52：优先取管线写回的真实库版本；回退也用生产真值 settings.rag_kb_version
        # （此前回退到 phase0a_mock_kb_version=kb_v1.0.0，与检索来源不符）
        knowledge_base_version = (
            diagnosis_payload.get("knowledge_base_version") or settings.rag_kb_version
        )
    else:
        diagnosis_payload = {
            "organ": result.organ,
            "findings": "Waiting for diagnosis system completion",
            "classification": "N/A",
            "recommendation": "N/A",
            "pending": True,
        }
        # ⚠️ A.91：这是**占位分支**（worker 还没写终态，前端显示"分析中"），
        # 这里的 2500ms 是 Phase 0A 的**预估展示值**，**不是真实耗时**。
        # 任何统计/P50/P99/性能门控**不得**取这个分支的值 —— 此时判读还没完成。
        # （记录在案的假数据来源清单：见 tests/unit/quality/test_a91_no_fake_evidence.py）
        processing_time_ms = settings.phase0a_mock_processing_time_ms
        model_version = settings.resolved_model_version
        llm_model = settings.resolved_llm_model
        # A.52：占位（processing）路径同样标注真实库版本
        knowledge_base_version = settings.rag_kb_version

    # 2026-09-28 接线审计修复：`quality_control` 与 `ai_diagnosis_failed` 此前是**硬编码占位**
    # （`self_rag_passed=False`、`consistency_check_passed=True`、`ai_diagnosis_failed=False`），
    # 会造成两个方向的误导：把"未评估"写成"未通过"、把"没检查过"写成"检查通过"。
    # 现在一律从管线写回的真实信号推导（`diagnosis_pipeline.py` 的 fusion_result）。
    #
    # 契约限制：`DiagnosisQualityControl` 的两个字段是**非可选 bool**（`schemas.py:206-207`），
    # 因此无法表达"未评估"这一第三态；本实现按"未评估 → 不得声称通过（False 保守）"处理。
    # 如需真正的三态语义，需要把契约改成 `bool | None`（影响面较大，另行排期）。
    fusion_payload = diagnosis_payload.get("fusion_result")
    if isinstance(fusion_payload, dict) and fusion_payload:
        self_rag_passed = bool(fusion_payload.get("self_rag_passed"))
        # 真实信号：融合层在一致性 CRITICAL 时置 review_reason="consistency_failed"
        # （见 app/fusion/layered_validator.py）。没有该 reason 才认为一致性未失败。
        consistency_check_passed = (
            fusion_payload.get("review_reason") != "consistency_failed"
        )
    else:
        # 没有融合结果（worker 未写入 / 占位 payload）→ 不得声称质检通过
        self_rag_passed = False
        consistency_check_passed = False
    # 真实失败语义：与 MonitorAgent._is_failure 的判据保持一致（monitor.py:138-142），
    # 否则 R3"同器官连续失败"窗口永远不会被写入。
    ai_diagnosis_failed = result_status in ("failed", "timeout")

    # ── A.88 A3：三个对外字段从**真实信号**推导（此前是硬编码常量）──────
    #
    # 原写法 `consistency_flag=CONSISTENT` / `fusion_status=NORMAL` 的危害不是
    # "少了个功能"，而是**对外表达了错误的事实**：一致性校验在生产上一次都没
    # 跑过（既往诊断从未接线、标准档指南对比拿不到 chunk），契约却始终声明
    # "一致"；融合层失败降级时也照样声明 "normal"。医生据此会跳过复核。
    has_fusion_result = isinstance(fusion_payload, dict) and bool(fusion_payload)
    fusion_status = FusionStatus.NORMAL if has_fusion_result else FusionStatus.BYPASSED

    if has_fusion_result:
        # "比过没有"读的是融合层写下的两个真实布尔（A3 在管线侧新增）；
        # 缺失/类型不对一律视为**未评估**，不猜。
        compared_prior = fusion_payload.get("compared_with_prior") is True
        compared_guidelines = fusion_payload.get("compared_with_guidelines") is True
        evaluated = compared_prior or compared_guidelines
        if evaluated:
            severity = fusion_payload.get("consistency_severity")
            consistency_flag = (
                ConsistencyFlag.INCONSISTENT
                if severity == "critical"
                else ConsistencyFlag.CONSISTENT
            )
        else:
            # 没有任何一路真的比过 → 不得声明 consistent（schema 允许 None）
            consistency_flag = None
    else:
        # 连融合结果都没有（worker 未写入 / 占位 payload）→ 未评估
        consistency_flag = None

    diagnosis_result = DiagnosisResult(
        request_id=request_id,
        status="completed" if result_status == "completed" else result_status,
        # A.88 A3：见上方推导（未评估 → None；只有真比过才给 consistent/inconsistent）
        consistency_flag=consistency_flag,
        desensitization_status=desensitization_status,
        consent_status_at_output=consent_status_at_output,
        # A.88 A3：有 findings.fusion_result → NORMAL，否则 BYPASSED
        fusion_status=fusion_status,
        diagnosis=diagnosis_payload,
        **clinical_attribute_fields(diagnosis_payload),
        # 分割旁路：findings 顶层的 segmentation（不进 clinical_attributes——
        # 它不是"医生直接信息需求 M1~M12"那一族，而是影像辅助结果）。
        # 缺失时给 {"available": False, "reason": "not_run"}，让前端能区分
        # "没做分割"与"分割了但没检出目标"。
        nodule_segmentation=(
            (diagnosis_payload or {}).get("segmentation")
            if isinstance((diagnosis_payload or {}).get("segmentation"), dict)
            else {"available": False, "reason": "not_run"}
        ),
        # T2a 分类旁路（02-1 S3）：与上方分割分支逐字同构 —— findings 顶层
        # t2a_classification（不进 clinical_attributes，同分割口径：它是影像
        # 辅助结果，不是 M1~M12 信息需求）。缺失时给
        # {"available": False, "reason": "not_run"}，让前端能区分"没做分类"
        # 与"分类了"；payload 内的 role=auxiliary_reference 必须原样透传，
        # 前端展示时必须带"辅助参考"标注（红线，不得与结论并列同层级）。
        t2a_classification=(
            (diagnosis_payload or {}).get("t2a_classification")
            if isinstance((diagnosis_payload or {}).get("t2a_classification"), dict)
            else {"available": False, "reason": "not_run"}
        ),
        # 相似病例（A.82）：统一走 `response_block()` —— 强制带"参考"标注与免责声明，
        # 避免某个分支漏标注；内部字段（patient_id/session_id）已在生成时剔除。
        similar_cases=similar_cases_response_block(
            (diagnosis_payload or {}).get("similar_cases")
        ),
        evidence=DiagnosisEvidence(
            knowledge_ids=result.knowledge_ids if hasattr(result, "knowledge_ids") and result.knowledge_ids else None,
            content=result.findings if result.findings else None,
        ),
        quality_control=DiagnosisQualityControl(
            confidence_score=result.confidence if hasattr(result, "confidence") and result.confidence else None,
            self_rag_passed=self_rag_passed,
            consistency_check_passed=consistency_check_passed,
        ),
        requires_manual_review=requires_manual_review,
        review_reason=review_reason,
        ai_diagnosis_failed=ai_diagnosis_failed,
        # A.88：`hallucination_severity` 现在**有生产者**了 ——
        # 管线在 FULL 档跑幻觉检测并把结果写进 `findings.fusion_result`
        # （`hallucination_severity` / `hallucination_checked` / `hallucination_reason`）。
        # 读取口径（三条都必须区分开，否则又回到"未评估写成通过"）：
        #   * `hallucination_checked` 不为 True → **未评估** → None（不编造）；
        #   * 已评估且有 severity 字符串 → 归一为枚举；
        #   * 已评估且干净（severity 缺省/None）→ None（"没有幻觉问题"在契约里
        #     没有独立取值，`HallucinationSeverity` 只有 severe/moderate/mild，
        #     故用 None 表示"未检出" —— 前端据 `quality_control` 区分展示）。
        hallucination_severity=_hallucination_severity_of(fusion_payload),
        # T-G1-DATA-02：按兼容矩阵真实计算，不再是恒为 "compatible" 的占位值
        version_compatibility=check_version_compatibility(
            model_version, knowledge_base_version
        ),
        processing_time_ms=processing_time_ms,
        model_version=model_version,
        llm_model=llm_model,
        knowledge_base_version=knowledge_base_version,
    )

    if result.status == "completed" and not result.result_pushed:
        await _push_diagnosis_result(result, request_id, db)

    try:
        if diagnosis_result.status == "completed":
            await RedisPool.setex(
                cache_key,
                RESULT_CACHE_TTL_SECONDS,
                json.dumps(diagnosis_result.model_dump()),
            )
        else:
            # processing 占位只缓存 1 秒（A.38 真机探针实测）：曾为 30s ——
            # 管线 5.8s 完成、DB 已更新，但轮询 26 秒内全部命中这份过期快照，
            # TTL 到期才回落 DB，医生感知 = 管线 6s + 缓存 27s ≈ 33s 等待。
            # processing 响应没有缓存价值，每次轮询都该看最新状态。
            await RedisPool.setex(
                cache_key,
                1,
                json.dumps(diagnosis_result.model_dump()),
            )
    except Exception as e:
        logger.warning("result_cache_set_failed", key=cache_key, error=str(e))

    if diagnosis_result.status == "processing":
        response.headers["Retry-After"] = "2"
        # 生成进度（A.43）+ 安全预览（A.46）：processing 中间态带上
        # 已生成字符数 + 已通过段落级 SG 的预览文本（best-effort）
        if request_id:
            from app.inference.base import (
                read_generation_preview_refused,
                read_generation_progress,
                read_generation_safe_preview,
            )

            progress = await read_generation_progress(request_id)
            if progress is not None:
                diagnosis_result.generation_progress_chars = progress
            refused = await read_generation_preview_refused(request_id)
            if refused:
                # 空串 = 明确的「已拒绝」信号（区别于 null=无预览）——前端隐藏预览
                diagnosis_result.generation_preview = ""
            else:
                preview = await read_generation_safe_preview(request_id)
                if preview:
                    diagnosis_result.generation_preview = preview[-400:]

    return diagnosis_result
