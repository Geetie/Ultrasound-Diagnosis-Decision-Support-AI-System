import asyncio
import json
import secrets
import time
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.config.database import AsyncSessionLocal
from app.config.settings import settings
from app.models.database import DiagnosisSession
from app.models.schemas import (
    AuthMessage,
    AuthOKMessage,
    DesensitizationStatus,
    DiagnosisResultMessage,
    EdgeNavResultMessage,
    FrameMessage,
    ImageQuality,
    NavigationAnnotation,
    NavigationMessage,
    NavigationRevocationMessage,
    PingMessage,
    PlaneAssessment,
    PongMessage,
    ReconnectAckMessage,
    RiskFlag,
    SessionTerminatedMessage,
    SuggestedAction,
    WebSocketMessageType,
)
from app.repositories import ConsentRepository, DeviceRepository, SessionRepository
from app.services.audit_service import AuditService
from app.services.navigation_upgrade import (
    collect_risk_labels,
    get_navigation_upgrade_service,
)
from app.services.safety_gateway_client import safety_gateway_client

logger = structlog.get_logger()
router = APIRouter(tags=["websocket-nav"])


WS_CLOSE_AUTH_FAILED = 4001
WS_CLOSE_AUTH_TIMEOUT = 4002

# 并发审查 P1-3：session 锁字典的 LRU 上界（防止长跑进程无界增长）
_MAX_SESSION_LOCKS = 4096

# 并发审查 P2：patient_id 缓存上界（原先只有 60s TTL 软失效、无清扫、无上界，
# 且 disconnect 不清理 → 条目数 = 历史 session 总数，单调增长）
_PATIENT_ID_CACHE_TTL = 60.0
_PATIENT_ID_CACHE_MAX = 4096

_nav_verification_semaphore: asyncio.Semaphore | None = None

# M2：自动升级的后台任务引用（持引用避免被 GC；非 asyncio 原语，无需绑定事件循环）
_pending_upgrade_tasks: set[asyncio.Task] = set()


def _get_nav_verification_semaphore() -> asyncio.Semaphore:
    global _nav_verification_semaphore
    if _nav_verification_semaphore is None:
        _nav_verification_semaphore = asyncio.Semaphore(settings.nav_verification_workers)
    return _nav_verification_semaphore


def _now_ms() -> int:
    """当前 epoch 毫秒（与边缘端 ``timestamp_ms`` 同口径）。"""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def record_nav_latency(message, started_monotonic: float) -> float | None:
    """记录一次导航链路延迟（T-GB-INT-02，验收：导航 P99 < 200ms）。

    时间戳口径按优先级依次尝试（两种口径在链路里并存，混用会让 P99 失真）：

    1. ``timestamp_ms``：边缘端上行消息的 **epoch 毫秒**（edge/websocket_server
       的 ``push_nav_result`` / ``push_frame``）；
    2. ``timestamp``：后端 ``get_timestamp()`` 的 **epoch 秒**（FrameMessage）；
    3. 都没有/不可信（时钟漂移、单位错误、未来时间）时退化为**后端处理耗时**
       （``started_monotonic``），并丢弃超出合理上限的样本。

    Returns:
        本次记录的延迟（秒）；埋点失败返回 ``None``（绝不影响导航链路）。
    """
    try:
        seconds: float | None = None

        sent_ms = getattr(message, "timestamp_ms", None)
        if sent_ms:
            delta_ms = _now_ms() - int(sent_ms)
            if 0 <= delta_ms <= settings.nav_latency_max_plausible_ms:
                seconds = delta_ms / 1000.0

        if seconds is None:
            sent_seconds = getattr(message, "timestamp", None)
            if sent_seconds:
                delta = get_timestamp() - int(sent_seconds)
                if 0 <= delta <= settings.nav_latency_max_plausible_ms / 1000.0:
                    seconds = float(delta)

        if seconds is None:
            seconds = time.monotonic() - started_monotonic

        seconds = max(0.0, seconds)
        from app.observability.metrics_collector import get_metrics_collector

        get_metrics_collector().record_nav_latency(seconds)
        return seconds
    except Exception as exc:
        logger.debug("nav_latency_metric_failed", error=str(exc))
        return None


def edge_labels_to_annotations(labels) -> list[NavigationAnnotation]:
    """边缘端标注（归一化 x/y/w/h）→ 前端导航标注（归一化 bbox）。

    M2：B+ 级标记随标注透传。只接受契约内的取值（``high`` /
    ``start_standard_evaluation``），其它字符串一律丢弃——避免未升级或
    异常边缘端把任意内容塞进对外契约。
    """
    annotations: list[NavigationAnnotation] = []
    for label in labels:
        risk_flag = getattr(label, "risk_flag", None)
        suggested_action = getattr(label, "suggested_action", None)
        annotations.append(
            NavigationAnnotation(
                label=label.label,
                confidence=float(label.confidence),
                bbox=[
                    float(label.x),
                    float(label.y),
                    float(label.width),
                    float(label.height),
                ],
                risk_flag=(
                    RiskFlag.HIGH if risk_flag == RiskFlag.HIGH.value else None
                ),
                suggested_action=(
                    SuggestedAction.START_STANDARD_EVALUATION
                    if suggested_action == SuggestedAction.START_STANDARD_EVALUATION.value
                    else None
                ),
            )
        )
    return annotations


def _schedule_auto_upgrade(
    *,
    session_id: str,
    frame_id: str,
    annotations: list[NavigationAnnotation],
    patient_id: str | None,
    organ: str | None = None,
) -> asyncio.Task | None:
    """M2：把 B+ 级高危标记的自动升级**移出导航热路径**。

    导航链路有 P99 < 200ms 的验收要求，而升级涉及多次 DB 查询 + 写入 +
    入队，绝不能同步等待。这里只做两件廉价的事：同步收集高危标签、同步
    预留配额（防抖在此生效，15fps 的连续帧不会派生一堆任务），真正的工作
    交给后台任务。

    Returns:
        已派生的任务；未触发时返回 ``None``。
    """
    if not settings.nav_auto_upgrade_enabled:
        return None
    if not collect_risk_labels(annotations):
        return None

    service = get_navigation_upgrade_service()
    rejection = service.try_reserve(session_id)
    if rejection:
        logger.debug(
            "nav_auto_upgrade_not_scheduled",
            session_id=session_id,
            frame_id=frame_id,
            reason=rejection,
        )
        return None

    task = asyncio.create_task(
        service.perform_upgrade(
            session_id=session_id,
            frame_id=frame_id,
            annotations=annotations,
            organ=organ,
            patient_id=patient_id,
        ),
        name="nav-auto-upgrade",
    )
    _pending_upgrade_tasks.add(task)
    task.add_done_callback(_pending_upgrade_tasks.discard)
    return task


def _observe_navigation_quality(
    websocket,
    *,
    session_id: str,
    frame_id: str,
    plane_assessment: PlaneAssessment | None,
    image_quality: ImageQuality | None,
) -> list[str]:
    """M1/M6：把本帧的切面/质量交给流程监控智能体。

    监控是纯内存操作（同步、廉价），但仍是"旁路"能力：监控对象缺失或
    内部异常都不得影响导航下发。
    """
    if plane_assessment is None and image_quality is None:
        return []
    try:
        app = getattr(websocket, "app", None)
        monitor = getattr(getattr(app, "state", None), "monitor_agent", None)
        observe = getattr(monitor, "observe_navigation_frame", None)
        if observe is None:
            return []
        return observe(
            session_id=session_id,
            frame_id=frame_id,
            plane_assessment=plane_assessment,
            image_quality=image_quality,
        )
    except Exception as exc:
        logger.debug("nav_quality_monitor_failed", error=str(exc))
        return []


async def deliver_navigation_annotations(
    *,
    manager,
    session_id: str,
    frame_id: str,
    annotations: list[NavigationAnnotation],
    message,
    started_monotonic: float,
    plane_assessment: PlaneAssessment | None = None,
    image_quality: ImageQuality | None = None,
    organ: str | None = None,
) -> bool:
    """同意/安全网关校验后把导航标注下发到前端，并记录链路延迟。

    后端推理路径（FrameMessage）与边缘端上行路径（nav_result）共用本函数，
    保证两条路径的安全检查、降级标记与埋点口径完全一致。

    M1/M6：切面评估与图像质量是**帧级**属性，随 ``NavigationMessage`` 下发
    （不逐标注重复）。M2：B+ 级高危标记的自动升级在网关放行、消息下发之后
    以**后台任务**发起，既不阻塞本条导航（P99 < 200ms），也不为被网关拒绝
    的输出发起诊断。

    Returns:
        True 已下发；False 被安全网关拒绝（已下发撤销消息）。
    """
    patient_id = await manager.get_patient_id(session_id)
    consent_status = "unknown"
    try:
        async with AsyncSessionLocal() as db:
            consent_repo = ConsentRepository(db)
            consent_record = await consent_repo.get_latest_consent(patient_id)
            if consent_record:
                consent_status = consent_record.status
    except Exception as e:
        logger.warning("nav_consent_lookup_failed", session_id=session_id, error=str(e))

    desensitization_status = DesensitizationStatus.FULL
    # 交给 SafetyGateway 的载荷：帧级属性同样要过网关（它们已在四处白名单
    # 中同口径登记；未部署评估头时不带键，保持载荷与改造前一致）。
    sg_output_data: dict[str, object] = {
        "annotations": [a.model_dump() for a in annotations]
    }
    if plane_assessment is not None:
        sg_output_data["plane_assessment"] = plane_assessment.model_dump()
    if image_quality is not None:
        sg_output_data["image_quality"] = image_quality.model_dump()

    try:
        async with _get_nav_verification_semaphore():
            sg_result = await asyncio.wait_for(
                safety_gateway_client.check_output(
                    session_id=session_id,
                    patient_id=patient_id,
                    output_data=sg_output_data,
                    consent_status_at_input=consent_status,
                ),
                timeout=settings.nav_safety_check_max_timeout_seconds,
            )
        if not sg_result.get("allowed", False):
            await manager.send_navigation_revocation(
                session_id,
                frame_id=frame_id,
                reason=sg_result.get("message", "Safety check failed"),
            )
            return False
        if sg_result.get("degraded_mode"):
            desensitization_status = DesensitizationStatus.DEGRADED
        elif sg_result.get("latency_ms", 0) > settings.nav_safety_check_normal_timeout_seconds * 1000:
            logger.info(
                "nav_safety_check_degraded_latency",
                session_id=session_id,
                latency_ms=sg_result.get("latency_ms"),
                normal_threshold_ms=settings.nav_safety_check_normal_timeout_seconds * 1000,
            )
            desensitization_status = DesensitizationStatus.DEGRADED
    except asyncio.TimeoutError:
        logger.warning("nav_safety_check_timeout", session_id=session_id)
        desensitization_status = DesensitizationStatus.DEGRADED
    except Exception as e:
        logger.warning("nav_safety_check_error", session_id=session_id, error=str(e))
        desensitization_status = DesensitizationStatus.DEGRADED

    nav_msg = NavigationMessage(
        type=WebSocketMessageType.NAVIGATION,
        timestamp=get_timestamp(),
        frame_id=frame_id,
        annotations=annotations,
        desensitization_status=desensitization_status,
        plane_assessment=plane_assessment,
        image_quality=image_quality,
    )
    await manager.send_personal_message(nav_msg.model_dump(), session_id)
    # T-GB-INT-02：导航链路延迟埋点（此前 nav_latency 指标只有定义与读取方）
    record_nav_latency(message, started_monotonic)

    # M2：网关放行后才考虑自动升级（被拒输出不发起深度诊断）
    _schedule_auto_upgrade(
        session_id=session_id,
        frame_id=frame_id,
        annotations=annotations,
        patient_id=patient_id,
        organ=organ,
    )
    return True


class AuthTokenManager:
    def __init__(self):
        self._tokens: dict[str, tuple] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._locks_lock = asyncio.Lock()

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        async with self._locks_lock:
            if session_id not in self._session_locks:
                self._session_locks[session_id] = asyncio.Lock()
                # 并发审查 P1-3：锁字典的 LRU 上界（长跑进程 session 增多时
                # 不得无界增长）。只回收未被持有的锁，绝不碰正在等/正在用的。
                while len(self._session_locks) > _MAX_SESSION_LOCKS:
                    victim = next(
                        (
                            sid
                            for sid, lk in self._session_locks.items()
                            if sid != session_id and not lk.locked()
                        ),
                        None,
                    )
                    if victim is None:
                        break
                    del self._session_locks[victim]
            return self._session_locks[session_id]

    def _cleanup_session_lock(self, session_id: str, has_active_connections: bool) -> None:
        """回收 session 锁（无活动连接时）。

        并发审查 P1-3：原实现无条件 ``del``，而调用点（``disconnect``）当时
        **正持有该锁**——删掉字典条目后，第三个协程会新建一把锁，与「仍持有
        旧锁引用并在等待」的协程同时进入临界区，互斥直接失效（_tokens 读改写
        lost update）。现改为：①调用点移出临界区后调用；②锁仍被持有时跳过；
        ③加 LRU 上界兜底，防止长跑进程字典无界增长。
        """
        if not has_active_connections:
            lock = self._session_locks.get(session_id)
            # 正被持有时不删（留到下一次 disconnect 再回收）
            if lock is None or not lock.locked():
                self._session_locks.pop(session_id, None)

    async def generate_token(self, session_id: str) -> str:
        lock = await self._get_session_lock(session_id)
        async with lock:
            token = secrets.token_urlsafe(32)
            self._tokens[session_id] = (token, time.time())
            return token

    async def validate_token(self, session_id: str, token: str) -> bool:
        ttl = settings.navigation_ws_auth_token_ttl_seconds
        lock = await self._get_session_lock(session_id)
        async with lock:
            if session_id not in self._tokens:
                return False
            stored_token, created_at = self._tokens[session_id]
            if time.time() - created_at > ttl:
                del self._tokens[session_id]
                return False
            return stored_token == token

    async def cleanup_expired_tokens(self) -> None:
        current_time = time.time()
        ttl = settings.navigation_ws_auth_token_ttl_seconds
        async with self._locks_lock:
            expired_sessions = [
                sid for sid, (_, created_at) in list(self._tokens.items())
                if current_time - created_at > ttl
            ]
        for sid in expired_sessions:
            lock = await self._get_session_lock(sid)
            async with lock:
                if sid in self._tokens and current_time - self._tokens[sid][1] > ttl:
                    del self._tokens[sid]


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[tuple, WebSocket] = {}
        self.session_index: dict[str, list] = {}
        self.auth_manager = AuthTokenManager()
        self.last_heartbeat: dict[tuple, float] = {}
        self.missed_pongs: dict[tuple, int] = {}
        self.device_sessions: dict[str, str] = {}
        self._redis_sub_task: asyncio.Task | None = None
        self._worker_id: str = secrets.token_hex(4)
        self._patient_id_cache: dict[str, tuple] = {}

    async def start_redis_subscriber(self) -> None:
        if not settings.websocket_redis_pubsub_enabled:
            return
        if self._redis_sub_task is not None:
            return
        self._redis_sub_task = asyncio.create_task(self._redis_subscribe_loop())
        logger.info("websocket_redis_pubsub_subscriber_started", worker_id=self._worker_id)

    async def stop_redis_subscriber(self) -> None:
        if self._redis_sub_task is not None:
            self._redis_sub_task.cancel()
            try:
                await self._redis_sub_task
            except asyncio.CancelledError:
                pass
            self._redis_sub_task = None
            logger.info("websocket_redis_pubsub_subscriber_stopped", worker_id=self._worker_id)

    async def _redis_subscribe_loop(self) -> None:
        from app.config.redis import RedisPool
        try:
            client = await RedisPool.get_client()
            pubsub = client.pubsub()
            await pubsub.psubscribe("ws:nav:*")
            async for message in pubsub.listen():
                if message["type"] not in ("pmessage",):
                    continue
                try:
                    data = json.loads(message["data"])
                    source_worker = data.get("_worker_id")
                    if source_worker == self._worker_id:
                        continue
                    session_id = data.get("session_id")
                    msg_payload = data.get("message")
                    if session_id and msg_payload:
                        await self._deliver_local(msg_payload, session_id)
                except Exception as e:
                    logger.warning("redis_pubsub_message_error", error=str(e))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("redis_pubsub_loop_error", error=str(e))

    async def _publish_to_redis(self, session_id: str, message: dict) -> None:
        from app.config.redis import RedisPool
        try:
            client = await RedisPool.get_client()
            payload = json.dumps({
                "session_id": session_id,
                "message": message,
                "_worker_id": self._worker_id,
            })
            await client.publish(f"ws:nav:{session_id}", payload)
        except Exception as e:
            logger.warning("redis_pubsub_publish_failed", session_id=session_id, error=str(e))

    async def _deliver_local(self, message: dict, session_id: str) -> None:
        # 并发审查 P1-1：原实现**持锁 await ws.send_json()**。send_json 是真网络
        # IO，弱网/客户端零窗口时会挂数百毫秒，把同 session 所有其它设备的下行
        # 消息一起串行阻塞（导航 P99 < 200ms 的验收直接击穿）。现改为：锁内只做
        # 连接快照，实际发送在锁外进行。
        lock = await self._get_session_lock(session_id)
        async with lock:
            keys = list(self.session_index.get(session_id, []))
            sockets = [
                (key, ws)
                for key in keys
                if (ws := self.active_connections.get(key)) is not None
            ]
        for key, ws in sockets:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.error("Failed to deliver local message", session_id=key[0], device_id=key[1], error=str(e))

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        return await self.auth_manager._get_session_lock(session_id)

    def _cleanup_session_lock(self, session_id: str) -> None:
        has_active = session_id in self.session_index
        if has_active:
            # 仍有活动连接时不做任何回收（含 LRU 裁剪），保持原语义
            self.auth_manager._cleanup_session_lock(session_id, True)
            return
        self.auth_manager._cleanup_session_lock(session_id, False)

    async def connect(self, websocket: WebSocket, session_id: str, device_id: str):
        await websocket.accept()
        key = (session_id, device_id)
        lock = await self._get_session_lock(session_id)
        over_limit = False
        current_count = 0
        async with lock:
            current_count = len(self.session_index.get(session_id, []))
            if current_count >= settings.nav_max_connections_per_session:
                over_limit = True
            else:
                self.active_connections[key] = websocket
                self.last_heartbeat[key] = time.time()
                if session_id not in self.session_index:
                    self.session_index[session_id] = []
                self.session_index[session_id].append(key)
        # 并发审查 P1-3：close 是网络 IO，移出临界区（否则拒绝路径会把同 session
        # 其它设备的下行消息阻塞到握手超时）。
        if over_limit:
            logger.warning(
                "WebSocket session connection limit exceeded",
                session_id=session_id,
                current_count=current_count,
                max_connections=settings.nav_max_connections_per_session,
            )
            await websocket.close(code=status.WS_1001_GOING_AWAY)
            return False
        logger.info("WebSocket connected", session_id=session_id, device_id=device_id)
        return True

    async def disconnect(self, session_id: str, device_id: str):
        key = (session_id, device_id)
        lock = await self._get_session_lock(session_id)
        async with lock:
            if key in self.active_connections:
                del self.active_connections[key]
            if key in self.last_heartbeat:
                del self.last_heartbeat[key]
            if key in self.missed_pongs:
                del self.missed_pongs[key]
            if session_id in self.session_index:
                self.session_index[session_id] = [
                    k for k in self.session_index[session_id] if k != key
                ]
                if not self.session_index[session_id]:
                    del self.session_index[session_id]
            for dev_id, sess_id in list(self.device_sessions.items()):
                if sess_id == session_id and dev_id == device_id:
                    del self.device_sessions[dev_id]
        # 并发审查 P1-3：锁回收必须在临界区之外（原来在锁内 del 字典条目，
        # 会让后续协程拿到新锁而与仍在等待旧锁的协程并发进入）。
        self._cleanup_session_lock(session_id)
        # 并发审查 P2：断开时清理 patient_id 缓存（原先只清连接表，缓存单调增长）
        self.invalidate_patient_id_cache(session_id)
        logger.info("WebSocket disconnected", session_id=session_id, device_id=device_id)

    async def send_personal_message(self, message: dict, session_id: str):
        await self._deliver_local(message, session_id)
        if settings.websocket_redis_pubsub_enabled:
            await self._publish_to_redis(session_id, message)

    async def generate_auth_token(self, session_id: str) -> str:
        return await self.auth_manager.generate_token(session_id)

    async def validate_auth_token(self, session_id: str, token: str) -> bool:
        return await self.auth_manager.validate_token(session_id, token)

    async def update_heartbeat(self, session_id: str, device_id: str):
        key = (session_id, device_id)
        lock = await self._get_session_lock(session_id)
        async with lock:
            self.last_heartbeat[key] = time.time()
            self.missed_pongs[key] = 0

    async def send_diagnosis_result(self, session_id: str, request_id: str):
        msg = DiagnosisResultMessage(
            type=WebSocketMessageType.DIAGNOSIS_RESULT,
            timestamp=get_timestamp(),
            request_id=request_id,
        )
        await self.send_personal_message(msg.model_dump(), session_id)

    async def send_session_terminated(
        self,
        session_id: str,
        reason: str,
        affected_organs: list | None = None,
        kb_version: str | None = None,
    ):
        msg = SessionTerminatedMessage(
            type=WebSocketMessageType.SESSION_TERMINATED,
            timestamp=get_timestamp(),
            reason=reason,
            affected_organs=affected_organs,
            kb_version=kb_version,
        )
        await self.send_personal_message(msg.model_dump(), session_id)

    async def send_navigation_revocation(self, session_id: str, frame_id: str, reason: str):
        msg = NavigationRevocationMessage(
            type=WebSocketMessageType.NAVIGATION_REVOCATION,
            timestamp=get_timestamp(),
            frame_id=frame_id,
            reason=reason,
        )
        await self.send_personal_message(msg.model_dump(), session_id)

    async def send_reconnect_ack(self, session_id: str, session_ttl_remaining_ms: int | None = None):
        msg = ReconnectAckMessage(
            type=WebSocketMessageType.RECONNECT_ACK,
            timestamp=get_timestamp(),
            session_id=session_id,
            session_ttl_remaining_ms=session_ttl_remaining_ms,
        )
        await self.send_personal_message(msg.model_dump(), session_id)

    async def check_heartbeat_timeout(self, timeout_seconds: int = 15, max_missed: int = 3) -> list:
        current_time = time.time()
        timeout_keys = []

        async with self.auth_manager._locks_lock:
            sessions_to_check = list(self.last_heartbeat.items())

        for key, last_time in sessions_to_check:
            if current_time - last_time > timeout_seconds:
                session_id = key[0]
                lock = await self._get_session_lock(session_id)
                async with lock:
                    if key not in self.last_heartbeat:
                        continue
                    self.missed_pongs[key] = self.missed_pongs.get(key, 0) + 1
                    self.last_heartbeat[key] = current_time
                    if self.missed_pongs[key] >= max_missed:
                        timeout_keys.append(key)
                    else:
                        logger.warning(
                            "Heartbeat missed",
                            session_id=key[0],
                            device_id=key[1],
                            missed_count=self.missed_pongs[key],
                            max_missed=max_missed,
                        )

        # 注意：cleanup_expired_tokens() 内部会自行获取 auth_manager._locks_lock，
        # 此处不能再包一层 async with 同一把锁（asyncio.Lock 不可重入），
        # 否则心跳巡检首次执行即死锁，并导致后续所有 token 操作阻塞。
        await self.auth_manager.cleanup_expired_tokens()

        return timeout_keys

    async def get_patient_id(self, session_id: str) -> str:
        now = time.time()
        if session_id in self._patient_id_cache:
            cached_pid, cached_at = self._patient_id_cache[session_id]
            if now - cached_at < _PATIENT_ID_CACHE_TTL:
                return cached_pid

        try:
            async with AsyncSessionLocal() as db:
                session_record = await db.get(DiagnosisSession, session_id)
                if session_record:
                    patient_id = session_record.patient_id or "unknown"
                    self._evict_patient_id_cache_if_needed(session_id)
                    self._patient_id_cache[session_id] = (patient_id, now)
                    return patient_id
        except Exception:
            logger.warning("nav_patient_id_lookup_failed", session_id=session_id)
        return "unknown"

    def _evict_patient_id_cache_if_needed(self, incoming_key: str) -> None:
        """缓存超上界时先淘汰已过期项，仍超限再淘汰最早写入项。

        并发审查 P2：原先只靠 60s TTL 软失效（只在再次访问时覆盖），无 maxsize、
        无后台清扫，条目数 = 历史 session 总数单调增长。
        """
        if len(self._patient_id_cache) < _PATIENT_ID_CACHE_MAX:
            return
        now = time.time()
        for key, (_, cached_at) in list(self._patient_id_cache.items()):
            if now - cached_at >= _PATIENT_ID_CACHE_TTL:
                self._patient_id_cache.pop(key, None)
        while len(self._patient_id_cache) >= _PATIENT_ID_CACHE_MAX and self._patient_id_cache:
            oldest = min(self._patient_id_cache, key=lambda k: self._patient_id_cache[k][1])
            if oldest == incoming_key:
                break
            self._patient_id_cache.pop(oldest, None)

    def invalidate_patient_id_cache(self, session_id: str) -> None:
        self._patient_id_cache.pop(session_id, None)


manager = ConnectionManager()


def get_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp())


@router.websocket("/api/v1/realtime/nav")
async def websocket_nav_endpoint(
    websocket: WebSocket,
    session_id: str = Query(...),
    device_id: str = Query(...),
):
    authenticated = False
    current_device_id = device_id

    try:
        connected = await manager.connect(websocket, session_id, device_id)
        if not connected:
            return

        try:
            data = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
            auth_msg = AuthMessage(**data)

            if auth_msg.device_id and auth_msg.api_key:
                async with AsyncSessionLocal() as db:
                    try:
                        device_repo = DeviceRepository(db)
                        device = await device_repo.authenticate_device(
                            auth_msg.device_id,
                            auth_msg.api_key
                        )

                        if device:
                            authenticated = True
                            current_device_id = auth_msg.device_id
                            manager.device_sessions[current_device_id] = session_id

                            audit_service = AuditService(db)
                            await audit_service.log_action(
                                action="websocket_auth",
                                actor=current_device_id,
                                session_id=session_id,
                                target_type="device",
                                target_id=current_device_id,
                                detail={"status": "success"},
                            )
                            await db.commit()
                        else:
                            logger.warning("Device authentication failed", device_id=auth_msg.device_id)
                    except Exception:
                        await db.rollback()
                        raise
            elif auth_msg.token and auth_msg.session_id:
                if await manager.validate_auth_token(auth_msg.session_id, auth_msg.token):
                    authenticated = True
                    current_device_id = "token_auth"

            if not authenticated:
                await websocket.close(code=WS_CLOSE_AUTH_FAILED)
                return

            session_ttl_remaining_ms = None
            async with AsyncSessionLocal() as db:
                try:
                    session_repo = SessionRepository(db)
                    session = await session_repo.get_session(session_id)
                    if not session:
                        logger.warning("Session not found", session_id=session_id)
                        await websocket.close(code=WS_CLOSE_AUTH_FAILED)
                        return
                    if session.updated_at:
                        elapsed = (datetime.now(timezone.utc) - session.updated_at).total_seconds()
                        session_ttl_remaining_ms = max(0, int((settings.session_max_ttl_seconds - elapsed) * 1000))
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise

            auth_token = await manager.generate_auth_token(session_id)

            auth_ok = AuthOKMessage(
                type=WebSocketMessageType.AUTH_OK,
                timestamp=get_timestamp(),
                session_id=session_id,
                token=auth_token
            )
            await websocket.send_json(auth_ok.model_dump())

            await manager.send_reconnect_ack(session_id, session_ttl_remaining_ms)

            logger.info("WebSocket authenticated", session_id=session_id, device_id=current_device_id)

        except asyncio.TimeoutError:
            logger.warning("WebSocket auth timeout", session_id=session_id)
            await websocket.close(code=WS_CLOSE_AUTH_TIMEOUT)
            return
        except Exception as e:
            logger.error("WebSocket authentication failed", session_id=session_id, error=str(e))
            await websocket.close(code=WS_CLOSE_AUTH_FAILED)
            return

        async def heartbeat_check():
            while True:
                await asyncio.sleep(settings.navigation_ws_heartbeat_interval_seconds)
                try:
                    key = (session_id, device_id)
                    current_missed = manager.missed_pongs.get(key, 0) + 1
                    manager.missed_pongs[key] = current_missed
                    if current_missed >= 3:
                        logger.warning("Heartbeat timeout", session_id=session_id, device_id=device_id)
                        await websocket.close(code=status.WS_1001_GOING_AWAY)
                        return
                    ping = PingMessage(
                        type=WebSocketMessageType.PING,
                        timestamp=get_timestamp(),
                    )
                    await websocket.send_json(ping.model_dump())
                except Exception:
                    return

        heartbeat_task = asyncio.create_task(heartbeat_check())

        while True:
            try:
                data = await websocket.receive_json()

                await manager.update_heartbeat(session_id, device_id)

                if data.get("type") == WebSocketMessageType.PING:
                    pong = PongMessage(
                        type=WebSocketMessageType.PONG,
                        timestamp=get_timestamp()
                    )
                    await websocket.send_json(pong.model_dump())

                elif data.get("type") == WebSocketMessageType.FRAME:
                    frame_msg = FrameMessage(**data)
                    # T-GB-INT-02：导航延迟埋点起点（后端处理耗时兜底口径）
                    frame_started = time.monotonic()

                    inference_engine = getattr(websocket.app.state, "inference_engine", None) if hasattr(websocket, "app") else None
                    if inference_engine:
                        try:
                            annotations = await inference_engine.get_navigation_annotations(session_id, frame_msg)
                        except Exception as e:
                            logger.warning("inference_engine_nav_failed", error=str(e), session_id=session_id)
                            annotations = []
                    else:
                        annotations = []
                        logger.info("nav_no_inference_engine_returning_empty", session_id=session_id)

                    delivered = await deliver_navigation_annotations(
                        manager=manager,
                        session_id=session_id,
                        frame_id=frame_msg.frame_id,
                        annotations=annotations,
                        message=frame_msg,
                        started_monotonic=frame_started,
                    )
                    if delivered:
                        # 后端推理路径当前不产出切面/质量（真机由边缘端提供），
                        # 这里保持接线以便后续接入即生效。
                        _observe_navigation_quality(
                            websocket,
                            session_id=session_id,
                            frame_id=frame_msg.frame_id,
                            plane_assessment=None,
                            image_quality=None,
                        )

                elif data.get("type") == "nav_result":
                    # T-GB-INT-02：边缘端上行导航结果（edge/websocket_server
                    # 的 push_nav_result 载荷）。此前后端没有任何消费者，
                    # 边缘端算出的标注到后端即被丢弃，导航链路是断的。
                    edge_msg = EdgeNavResultMessage(**data)
                    edge_started = time.monotonic()
                    edge_session_id = edge_msg.session_id or session_id
                    delivered = await deliver_navigation_annotations(
                        manager=manager,
                        session_id=edge_session_id,
                        frame_id=edge_msg.frame_id,
                        annotations=edge_labels_to_annotations(edge_msg.labels),
                        message=edge_msg,
                        started_monotonic=edge_started,
                        plane_assessment=edge_msg.plane_assessment,
                        image_quality=edge_msg.image_quality,
                    )
                    if delivered:
                        # M1/M6：仅在网关放行（即医生确实看到这一帧）后监控
                        _observe_navigation_quality(
                            websocket,
                            session_id=edge_session_id,
                            frame_id=edge_msg.frame_id,
                            plane_assessment=edge_msg.plane_assessment,
                            image_quality=edge_msg.image_quality,
                        )

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error("WebSocket error", session_id=session_id, error=str(e))
                break

    finally:
        if 'heartbeat_task' in locals():
            heartbeat_task.cancel()
        await manager.disconnect(session_id, device_id)

        try:
            async with AsyncSessionLocal() as db:
                try:
                    audit_service = AuditService(db)
                    await audit_service.log_action(
                        action="websocket_disconnect",
                        actor=current_device_id or "unknown",
                        session_id=session_id,
                        target_type="session",
                        target_id=session_id,
                        detail={"status": "disconnected"},
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
        except Exception as e:
            logger.error("Failed to log audit", error=str(e))
