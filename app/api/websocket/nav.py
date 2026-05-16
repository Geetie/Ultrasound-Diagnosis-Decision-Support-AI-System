from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, WebSocketException, status
from datetime import datetime, timezone
from typing import Dict, Set, Optional
import json
import time
import secrets
import asyncio
import structlog

from app.models.schemas import (
    BaseWebSocketMessage,
    FrameMessage,
    NavigationMessage,
    PingMessage,
    PongMessage,
    AuthMessage,
    AuthOKMessage,
    DiagnosisResultMessage,
    SessionTerminatedMessage,
    NavigationRevocationMessage,
    ReconnectAckMessage,
    WebSocketMessageType,
    NavigationAnnotation,
    DesensitizationStatus,
)
from app.config.database import AsyncSessionLocal
from app.config.settings import settings
from app.repositories import DeviceRepository, SessionRepository, ConsentRepository
from app.models.database import DiagnosisSession
from app.services.audit_service import AuditService
from app.services.safety_gateway_client import safety_gateway_client

logger = structlog.get_logger()
router = APIRouter(tags=["websocket-nav"])


WS_CLOSE_AUTH_FAILED = 4001
WS_CLOSE_AUTH_TIMEOUT = 4002

_nav_verification_semaphore: Optional[asyncio.Semaphore] = None


def _get_nav_verification_semaphore() -> asyncio.Semaphore:
    global _nav_verification_semaphore
    if _nav_verification_semaphore is None:
        _nav_verification_semaphore = asyncio.Semaphore(settings.nav_verification_workers)
    return _nav_verification_semaphore


class AuthTokenManager:
    def __init__(self):
        self._tokens: Dict[str, tuple] = {}
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._locks_lock = asyncio.Lock()

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        async with self._locks_lock:
            if session_id not in self._session_locks:
                self._session_locks[session_id] = asyncio.Lock()
            return self._session_locks[session_id]

    def _cleanup_session_lock(self, session_id: str, has_active_connections: bool) -> None:
        if not has_active_connections and session_id in self._session_locks:
            del self._session_locks[session_id]

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
        self.active_connections: Dict[tuple, WebSocket] = {}
        self.session_index: Dict[str, list] = {}
        self.auth_manager = AuthTokenManager()
        self.last_heartbeat: Dict[tuple, float] = {}
        self.missed_pongs: Dict[tuple, int] = {}
        self.device_sessions: Dict[str, str] = {}
        self._redis_sub_task: Optional[asyncio.Task] = None
        self._worker_id: str = secrets.token_hex(4)
        self._patient_id_cache: Dict[str, tuple] = {}

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
        lock = await self._get_session_lock(session_id)
        async with lock:
            keys = self.session_index.get(session_id, [])
            for key in keys:
                ws = self.active_connections.get(key)
                if ws:
                    try:
                        await ws.send_json(message)
                    except Exception as e:
                        logger.error("Failed to deliver local message", session_id=key[0], device_id=key[1], error=str(e))

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        return await self.auth_manager._get_session_lock(session_id)

    def _cleanup_session_lock(self, session_id: str) -> None:
        has_active = session_id in self.session_index
        self.auth_manager._cleanup_session_lock(session_id, has_active)

    async def connect(self, websocket: WebSocket, session_id: str, device_id: str):
        await websocket.accept()
        key = (session_id, device_id)
        lock = await self._get_session_lock(session_id)
        async with lock:
            current_count = len(self.session_index.get(session_id, []))
            if current_count >= settings.nav_max_connections_per_session:
                logger.warning(
                    "WebSocket session connection limit exceeded",
                    session_id=session_id,
                    current_count=current_count,
                    max_connections=settings.nav_max_connections_per_session,
                )
                await websocket.close(code=status.WS_1001_GOING_AWAY)
                return False
            self.active_connections[key] = websocket
            self.last_heartbeat[key] = time.time()
            if session_id not in self.session_index:
                self.session_index[session_id] = []
            self.session_index[session_id].append(key)
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
            self._cleanup_session_lock(session_id)
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
        affected_organs: Optional[list] = None,
        kb_version: Optional[str] = None,
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

    async def send_reconnect_ack(self, session_id: str, session_ttl_remaining_ms: Optional[int] = None):
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

        async with self.auth_manager._locks_lock:
            await self.auth_manager.cleanup_expired_tokens()

        return timeout_keys

    async def get_patient_id(self, session_id: str) -> str:
        _PATIENT_ID_CACHE_TTL = 60.0
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
                    self._patient_id_cache[session_id] = (patient_id, now)
                    return patient_id
        except Exception:
            logger.warning("nav_patient_id_lookup_failed", session_id=session_id)
        return "unknown"

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
                    try:
                        async with _get_nav_verification_semaphore():
                            sg_result = await asyncio.wait_for(
                                safety_gateway_client.check_output(
                                    session_id=session_id,
                                    patient_id=patient_id,
                                    output_data={"annotations": [a.model_dump() for a in annotations]},
                                    consent_status_at_input=consent_status,
                                ),
                                timeout=settings.nav_safety_check_max_timeout_seconds,
                            )
                        if not sg_result.get("allowed", False):
                            await manager.send_navigation_revocation(
                                session_id,
                                frame_id=frame_msg.frame_id,
                                reason=sg_result.get("message", "Safety check failed"),
                            )
                            continue
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
                        frame_id=frame_msg.frame_id,
                        annotations=annotations,
                        desensitization_status=desensitization_status
                    )
                    await manager.send_personal_message(nav_msg.model_dump(), session_id)

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
