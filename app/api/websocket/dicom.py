import asyncio
import binascii
import json
import struct
import time
from collections import OrderedDict
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.api.v1.imaging import parse_and_validate_dicom, validate_dicom_format
from app.api.websocket.nav import WS_CLOSE_AUTH_FAILED, WS_CLOSE_AUTH_TIMEOUT, get_timestamp
from app.api.websocket.nav import manager as nav_manager
from app.config.database import AsyncSessionLocal
from app.config.settings import settings
from app.models.schemas import AuthMessage, AuthOKMessage, PingMessage, WebSocketMessageType
from app.services.audit_service import AuditService

logger = structlog.get_logger()
router = APIRouter(tags=["websocket-dicom"])

MAX_RETRANSMIT_PER_FRAME = settings.dicom_max_retransmit_per_frame
MAX_RETRANSMIT_FRAME_RANGE = settings.dicom_max_retransmit_frame_range
MAX_DICOM_FRAME_PAYLOAD_BYTES = 500 * 1024 * 1024


class FrameBuffer:
    def __init__(self, capacity: int = 90):
        self._capacity = capacity
        self._buffer: OrderedDict[int, bytes] = OrderedDict()

    def put(self, frame_index: int, data: bytes) -> None:
        if frame_index in self._buffer:
            del self._buffer[frame_index]
        self._buffer[frame_index] = data
        while len(self._buffer) > self._capacity:
            self._buffer.popitem(last=False)

    def get(self, frame_index: int) -> bytes | None:
        return self._buffer.get(frame_index)

    def get_range(self, start: int, end: int) -> dict[int, bytes]:
        result = {}
        for idx in range(start, end + 1):
            data = self._buffer.get(idx)
            if data is not None:
                result[idx] = data
        return result

    def clear(self) -> None:
        self._buffer.clear()


def calculate_crc32(data: bytes) -> int:
    return binascii.crc32(data) & 0xffffffff


def decompress_zstd(data: bytes) -> bytes | None:
    try:
        import zstandard as zstd
        decompressor = zstd.ZstdDecompressor()
        return decompressor.decompress(data, max_output_size=MAX_DICOM_FRAME_PAYLOAD_BYTES)
    except Exception:  # silent-ok: 解压失败返回 None，调用方（L295）已判 None 并 warning
        return None


@router.websocket("/api/v1/realtime/dicom")
async def websocket_dicom_endpoint(
    websocket: WebSocket,
    session_id: str = Query(...),
    device_id: str = Query(...),
):
    authenticated = False
    frame_count = 0
    last_heartbeat_time = time.monotonic()
    missed_pongs = 0
    max_missed_pongs = 3
    retransmit_counts: dict[int, int] = {}
    latest_frame_index = -1
    compression_enabled = False
    frame_buffer = FrameBuffer(capacity=settings.dicom_frame_buffer_capacity)

    compression_header = websocket.headers.get("x-compression", "")
    if compression_header.lower() == "zstd":
        try:
            import zstandard as zstd
            compression_enabled = True
            logger.info("DICOM channel zstd compression enabled", session_id=session_id)
        except ImportError:
            logger.warning("zstd compression requested but zstandard not installed", session_id=session_id)

    try:
        await websocket.accept()

        try:
            data = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
            auth_msg = AuthMessage(**data)

            if not auth_msg.token:
                logger.warning("DICOM channel auth failed: no token provided", session_id=session_id)
                await websocket.close(code=WS_CLOSE_AUTH_FAILED)
                return

            if auth_msg.session_id and auth_msg.session_id != session_id:
                logger.warning(
                    "DICOM channel auth failed: session_id mismatch",
                    url_session_id=session_id,
                    auth_session_id=auth_msg.session_id,
                )
                await websocket.close(code=WS_CLOSE_AUTH_FAILED)
                return

            if not await nav_manager.validate_auth_token(session_id, auth_msg.token):
                logger.warning("DICOM channel auth failed: invalid token", session_id=session_id)
                await websocket.close(code=WS_CLOSE_AUTH_FAILED)
                return

            authenticated = True

            async with AsyncSessionLocal() as db:
                try:
                    audit_service = AuditService(db)
                    await audit_service.log_action(
                        action="dicom_channel_auth",
                        actor=device_id,
                        session_id=session_id,
                        target_type="channel",
                        target_id="dicom",
                        detail={"status": "success"},
                    )
                    await db.commit()
                except Exception as e:  # A.60：审计写库失败须留痕（"该记的没记"）
                    await db.rollback()
                    logger.warning(
                        "dicom_channel_auth_audit_failed",
                        session_id=session_id,
                        error=str(e),
                        error_type=type(e).__name__,
                    )

            auth_ok = AuthOKMessage(
                type=WebSocketMessageType.AUTH_OK,
                timestamp=int(datetime.now(timezone.utc).timestamp()),
                session_id=session_id
            )
            await websocket.send_json(auth_ok.model_dump())

            logger.info("DICOM WebSocket channel authenticated", session_id=session_id)

        except asyncio.TimeoutError:
            logger.warning("DICOM channel auth timeout", session_id=session_id)
            await websocket.close(code=WS_CLOSE_AUTH_TIMEOUT)
            return
        except Exception as e:
            logger.error("DICOM channel authentication failed", session_id=session_id, error=str(e))
            await websocket.close(code=WS_CLOSE_AUTH_FAILED)
            return

        heartbeat_interval = settings.navigation_ws_heartbeat_interval_seconds

        async def heartbeat_sender():
            nonlocal missed_pongs
            while True:
                await asyncio.sleep(heartbeat_interval)
                try:
                    ping = PingMessage(
                        type=WebSocketMessageType.PING,
                        timestamp=get_timestamp(),
                    )
                    await websocket.send_json(ping.model_dump())
                    missed_pongs += 1
                    if missed_pongs >= max_missed_pongs:
                        logger.warning(
                            "DICOM channel heartbeat timeout",
                            session_id=session_id,
                            missed_pongs=missed_pongs,
                        )
                        await websocket.close(code=status.WS_1001_GOING_AWAY)
                        return
                except Exception as e:  # A.60：心跳发送失败原先静默 return（现象是"影像不再更新"）
                    logger.warning(
                        "dicom_channel_heartbeat_send_failed",
                        session_id=session_id,
                        error=str(e),
                        error_type=type(e).__name__,
                    )
                    return

        heartbeat_task = asyncio.create_task(heartbeat_sender())

        while True:
            try:
                message = await websocket.receive()

                if message["type"] == "websocket.disconnect":
                    break

                if message["type"] == "websocket.receive":
                    if "text" in message:
                        try:
                            data = json.loads(message["text"])
                            if data.get("type") == WebSocketMessageType.PONG:
                                last_heartbeat_time = time.monotonic()
                                missed_pongs = 0
                            continue
                        except (json.JSONDecodeError, KeyError):  # silent-ok: 非 PONG 控制帧无需处理
                            continue

                    binary_data = message.get("bytes")
                    if not binary_data:
                        continue

                if len(binary_data) < 9:
                    logger.warning("Frame too small", session_id=session_id, size=len(binary_data))
                    continue

                frame_index = struct.unpack("<i", binary_data[0:4])[0]
                flags = binary_data[4]
                dicom_length = struct.unpack("<i", binary_data[5:9])[0]

                if frame_index < 0:
                    logger.warning(
                        "Negative frame_index rejected",
                        session_id=session_id,
                        frame_index=frame_index,
                    )
                    await websocket.send_json({
                        "type": "error",
                        "error_code": "E1001",
                        "error_message": "frame_index must be non-negative",
                        "frame_index": frame_index,
                    })
                    continue

                if dicom_length < 0:
                    logger.warning(
                        "Negative dicom_length rejected",
                        session_id=session_id,
                        dicom_length=dicom_length,
                    )
                    continue

                if dicom_length > MAX_DICOM_FRAME_PAYLOAD_BYTES:
                    logger.warning(
                        "dicom_length exceeds maximum allowed payload size",
                        session_id=session_id,
                        dicom_length=dicom_length,
                        max_allowed=MAX_DICOM_FRAME_PAYLOAD_BYTES,
                    )
                    continue

                expected_min_length = 9 + dicom_length + 4
                if len(binary_data) < expected_min_length:
                    logger.warning(
                        "Frame length mismatch (missing CRC32)",
                        session_id=session_id,
                        expected=expected_min_length,
                        actual=len(binary_data),
                    )
                    continue

                dicom_data = binary_data[9:9 + dicom_length]
                received_crc = struct.unpack("<I", binary_data[9 + dicom_length:9 + dicom_length + 4])[0]

                is_key_frame = bool(flags & 0x01)
                priority = (flags >> 1) & 0x01
                is_compressed = bool(flags & 0x04)

                computed_crc = calculate_crc32(dicom_data)

                if received_crc != computed_crc:
                    if latest_frame_index >= 0 and frame_index < latest_frame_index - MAX_RETRANSMIT_FRAME_RANGE:
                        logger.warning(
                            "CRC failed frame too old for retransmission",
                            session_id=session_id,
                            frame_index=frame_index,
                            latest=latest_frame_index,
                        )
                        continue
                    retransmit_counts[frame_index] = retransmit_counts.get(frame_index, 0) + 1
                    if retransmit_counts[frame_index] > MAX_RETRANSMIT_PER_FRAME:
                        logger.error(
                            "CRC32 retransmit limit exceeded, dropping frame",
                            session_id=session_id,
                            frame_index=frame_index,
                            attempts=retransmit_counts[frame_index],
                        )
                        del retransmit_counts[frame_index]
                        continue
                    logger.warning(
                        "CRC32 mismatch, requesting retransmission",
                        session_id=session_id,
                        frame_index=frame_index,
                        expected_crc=computed_crc,
                        received_crc=received_crc,
                        attempt=retransmit_counts[frame_index],
                    )
                    await websocket.send_json({
                        "type": "retransmit_request",
                        "frame_index": frame_index,
                    })
                    continue

                if is_compressed and compression_enabled:
                    decompressed = decompress_zstd(dicom_data)
                    if decompressed is not None:
                        dicom_data = decompressed
                    else:
                        logger.warning(
                            "Frame zstd decompression failed, dropping frame",
                            session_id=session_id,
                            frame_index=frame_index,
                        )
                        continue

                if is_key_frame and len(dicom_data) >= 132:
                    is_valid_format, format_error = validate_dicom_format(dicom_data)
                    if is_valid_format:
                        _, validation_error, _ = parse_and_validate_dicom(dicom_data)
                        if validation_error:
                            logger.warning(
                                "Key frame DICOM content validation failed",
                                session_id=session_id,
                                frame_index=frame_index,
                                error=validation_error,
                            )
                            await websocket.send_json({
                                "type": "error",
                                "error_code": "E1004",
                                "error_message": validation_error,
                                "frame_index": frame_index,
                            })
                            continue

                frame_count += 1
                last_heartbeat_time = time.monotonic()
                missed_pongs = 0
                if frame_index > latest_frame_index:
                    latest_frame_index = frame_index
                frame_buffer.put(frame_index, binary_data)
                logger.debug(
                    "DICOM frame received",
                    session_id=session_id,
                    frame_index=frame_index,
                    is_key_frame=is_key_frame,
                    priority=priority,
                    frame_size=len(dicom_data),
                )

                try:
                    await nav_manager.send_personal_message(
                        {
                            "type": "dicom_frame_received",
                            "timestamp": get_timestamp(),
                            "session_id": session_id,
                            "frame_index": frame_index,
                            "is_key_frame": is_key_frame,
                            "priority": priority,
                            "frame_size": len(dicom_data),
                        },
                        session_id,
                    )
                except Exception as e:
                    logger.warning("dicom_frame_forward_to_nav_failed", session_id=session_id, error=str(e))

                if frame_count % 100 == 0:
                    try:
                        async with AsyncSessionLocal() as db:
                            try:
                                audit_service = AuditService(db)
                                await audit_service.log_action(
                                    action="dicom_frame_received",
                                    actor="dicom_channel",
                                    session_id=session_id,
                                    target_type="frame",
                                    target_id=str(frame_index),
                                    detail={"frame_count": frame_count},
                                )
                                await db.commit()
                            except Exception as e:
                                # A.60：帧审计写库失败原先静默 rollback（"该记的没记"必须留痕；
                                # 不 raise —— 审计失败不该打断正在推流的影像通道）
                                await db.rollback()
                                logger.warning(
                                    "dicom_frame_audit_failed",
                                    session_id=session_id,
                                    frame_count=frame_count,
                                    error=str(e),
                                    error_type=type(e).__name__,
                                )
                    except Exception as e:
                        logger.warning("dicom_frame_audit_outer_failed", error=str(e))

            except WebSocketDisconnect:  # silent-ok: 医生关页面/断网属正常断开（审计在 finally 里写）
                break
            except Exception as e:
                logger.error("DICOM channel error", session_id=session_id, error=str(e))
                break

    finally:
        if 'heartbeat_task' in locals():
            heartbeat_task.cancel()
        try:
            async with AsyncSessionLocal() as db:
                try:
                    audit_service = AuditService(db)
                    await audit_service.log_action(
                        action="dicom_channel_disconnect",
                        actor="dicom_channel",
                        session_id=session_id,
                        target_type="channel",
                        target_id="dicom",
                        detail={"frame_count": frame_count, "status": "disconnected"},
                    )
                    await db.commit()
                except Exception as e:  # A.60：断开审计写失败须留痕（原先只 rollback）
                    await db.rollback()
                    logger.warning(
                        "dicom_channel_disconnect_audit_failed",
                        session_id=session_id,
                        error=str(e),
                        error_type=type(e).__name__,
                    )
        except Exception as e:
            logger.error("Failed to log audit", error=str(e))
