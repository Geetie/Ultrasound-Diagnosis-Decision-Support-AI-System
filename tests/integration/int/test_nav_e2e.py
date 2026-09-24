"""T-GB-INT-02 边缘端 → 后端 → 前端 导航全链路集成。

覆盖三跳：

1. **边缘端**：``EdgePipeline.on_frame`` → ``NavigationInferenceService``
   推理 + 本地安全校验 → 上行（``FrameSink``）；
2. **后端**：``app/api/websocket/nav`` 的帧处理把标注经安全网关校验后
   下发，并记录导航延迟（``record_nav_latency``）；
3. **前端**：``ConnectionManager`` 把导航消息投递到已连接终端。

验收：导航链路 P99 < 200ms（`03-接口规范` / `05-可观测性与运维`）。
延迟埋点此前只有指标定义与看板读取方、没有任何写入方，本文件同时锁定
该埋点不再回退（回退即失败）。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.api.websocket.nav import (
    deliver_navigation_annotations,
    edge_labels_to_annotations,
    record_nav_latency,
)
from app.models.schemas import (
    EdgeNavLabel,
    EdgeNavResultMessage,
    FrameMessage,
    WebSocketMessageType,
)
from edge.navigation import NavigationInferenceService
from edge.pipeline import EdgePipeline
from edge.protocols import Frame, NavLabel

NAV_P99_BUDGET_MS = 200.0
FRAME_COUNT = 200


class _FakeBackend:
    """确定性假后端：固定延迟 + 归一化坐标标注。"""

    name = "fake_nav"

    def __init__(self, infer_cost_ms: float = 3.0, labels: Optional[List[NavLabel]] = None) -> None:
        self.loaded = 0
        self.calls = 0
        self._cost = infer_cost_ms
        # 标注名必须落在 edge.safety 的白名单内（organ_name / boundary / ...）
        self._labels = labels or [
            NavLabel(label="organ_name", x=0.30, y=0.25, width=0.20, height=0.18, confidence=0.92),
            NavLabel(label="boundary", x=0.60, y=0.55, width=0.15, height=0.12, confidence=0.81),
        ]

    def load(self) -> None:
        self.loaded += 1

    def infer(self, frame: Frame) -> List[NavLabel]:
        self.calls += 1
        # 模拟 TensorRT 同步推理耗时（真实耗时在真机；此处只验证链路与预算）
        deadline = time.perf_counter() + self._cost / 1000.0
        while time.perf_counter() < deadline:
            pass
        return list(self._labels)

    def close(self) -> None:
        pass


class _RecordingSink:
    """上行通道替身：记录边缘端推上来的导航结果。"""

    def __init__(self) -> None:
        self.results: List[Any] = []

    async def push_nav_result(self, result) -> bool:
        self.results.append(result)
        return True

    async def push_frame(self, frame, payload) -> None:
        return None


def _frame(index: int, *, session_id: str = "sess_nav") -> Frame:
    pixels = np.zeros((512, 512), dtype=np.uint8)
    # 造一点结构，避免全 0 帧掩盖预处理异常
    pixels[100:200, 120:220] = 180
    return Frame(
        frame_id=f"f_{index}",
        session_id=session_id,
        index=index,
        pixels=pixels,
        received_at_ms=time.monotonic() * 1000.0,
    )


def _percentile(values: List[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (pct / 100.0) * (len(ordered) - 1)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


# ----------------------------------------------------------------------
# 跳 1+2：边缘端推理 → 上行
# ----------------------------------------------------------------------


class TestEdgeToBackend:
    @pytest.mark.asyncio
    async def test_edge_pipeline_p99_within_budget(self):
        backend = _FakeBackend(infer_cost_ms=3.0)
        nav = NavigationInferenceService(backend, target_fps=1000.0, latency_window=512)
        sink = _RecordingSink()
        pipeline = EdgePipeline(nav, uplink=sink)

        edge_metrics: List[float] = []
        collector = MagicMock()
        collector.record_edge_nav_latency = MagicMock(side_effect=edge_metrics.append)

        latencies_ms: List[float] = []
        with patch(
            "app.observability.metrics_collector.get_metrics_collector",
            lambda: collector,
        ):
            for index in range(FRAME_COUNT):
                started = time.perf_counter()
                result = await pipeline.on_frame(_frame(index), now=index * 1.0)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                assert result is not None
                latencies_ms.append(elapsed_ms)

        p99 = _percentile(latencies_ms, 99.0)
        assert p99 < NAV_P99_BUDGET_MS, f"边缘推理链路 P99={p99:.1f}ms 超预算"
        assert len(sink.results) == FRAME_COUNT

        # 边缘端延迟埋点（指标此前无写入方）
        assert len(edge_metrics) == FRAME_COUNT
        assert all(seconds > 0 for seconds in edge_metrics)

        stats = nav.stats()
        assert stats["frames_inferred"] == FRAME_COUNT
        assert stats["p99_latency_ms"] is not None
        assert stats["backend"] == "fake_nav"

    @pytest.mark.asyncio
    async def test_edge_local_safety_filters_out_of_range_labels(self):
        """坐标越界 / 非白名单标签必须在边缘端就被拦下（T-GB-EDG-03）。"""
        backend = _FakeBackend(
            infer_cost_ms=0.0,
            labels=[
                NavLabel(label="organ_name", x=0.2, y=0.2, width=0.1, height=0.1, confidence=0.9),
                NavLabel(label="unknown_label", x=0.3, y=0.3, width=0.1, height=0.1, confidence=0.9),
                NavLabel(label="boundary", x=1.4, y=0.2, width=0.1, height=0.1, confidence=0.9),
            ],
        )
        nav = NavigationInferenceService(backend, target_fps=1000.0)
        sink = _RecordingSink()
        pipeline = EdgePipeline(nav, uplink=sink)

        result = await pipeline.on_frame(_frame(0), now=0.0)
        assert result is not None
        assert [label.label for label in result.labels] == ["organ_name"]
        assert len(sink.results) == 1

    @pytest.mark.asyncio
    async def test_isolated_device_stops_uplink_but_keeps_local_result(self):
        """隔离 ≠ 停机：本地仍出叠加结果，只是不上行。"""
        device_manager = MagicMock()
        device_manager.should_uplink = False
        device_manager.state = MagicMock(value="isolated")
        nav = NavigationInferenceService(_FakeBackend(infer_cost_ms=0.0), target_fps=1000.0)
        sink = _RecordingSink()
        pipeline = EdgePipeline(nav, uplink=sink, device_manager=device_manager)

        result = await pipeline.on_frame(_frame(0), now=0.0)

        assert result is not None and result.labels
        assert sink.results == []
        assert pipeline.status()["results_produced"] == 1
        assert pipeline.status()["push_suppressed"] == 1


# ----------------------------------------------------------------------
# 跳 2：后端延迟埋点
# ----------------------------------------------------------------------


class TestBackendLatencyInstrumentation:
    def _collector(self):
        recorded: List[float] = []
        collector = MagicMock()
        collector.record_nav_latency = MagicMock(side_effect=recorded.append)
        return collector, recorded

    def test_latency_from_edge_timestamp_ms(self):
        """边缘端上行消息的 epoch 毫秒口径（timestamp_ms）。"""
        collector, recorded = self._collector()
        from app.api.websocket import nav as nav_module

        edge_msg = EdgeNavResultMessage(
            frame_id="f_edge",
            session_id="sess_nav",
            labels=[],
            timestamp_ms=nav_module._now_ms() - 120,
        )
        with patch(
            "app.observability.metrics_collector.get_metrics_collector",
            lambda: collector,
        ):
            seconds = record_nav_latency(edge_msg, time.monotonic())

        assert seconds is not None
        assert 0.09 <= seconds <= 0.3
        assert len(recorded) == 1

    def test_latency_from_backend_seconds_timestamp(self):
        """后端 FrameMessage.timestamp 为 epoch **秒**（get_timestamp 口径）。"""
        collector, recorded = self._collector()
        from app.api.websocket import nav as nav_module

        fixed_now = 1_700_000_000
        frame_msg = FrameMessage(
            type=WebSocketMessageType.FRAME,
            timestamp=fixed_now - 1,
            frame_id="f_1",
            frame_index=1,
        )
        with patch.object(nav_module, "get_timestamp", lambda: fixed_now), patch(
            "app.observability.metrics_collector.get_metrics_collector",
            lambda: collector,
        ):
            seconds = record_nav_latency(frame_msg, time.monotonic())

        assert seconds is not None
        assert abs(seconds - 1.0) < 0.001
        assert len(recorded) == 1

    def test_latency_falls_back_to_backend_elapsed(self):
        collector, _recorded = self._collector()

        frame_msg = FrameMessage(
            type=WebSocketMessageType.FRAME, timestamp=None, frame_id="f_2", frame_index=2
        )
        started = time.monotonic() - 0.05  # 后端已处理 50ms
        with patch(
            "app.observability.metrics_collector.get_metrics_collector",
            lambda: collector,
        ):
            seconds = record_nav_latency(frame_msg, started)

        assert seconds is not None
        assert 0.04 <= seconds <= 0.2  # 含 Windows 时钟粒度余量

    def test_implausible_clock_is_rejected(self):
        """未来时间戳（时钟漂移/单位错误）不得污染 P99。"""
        collector, _recorded = self._collector()
        from app.api.websocket import nav as nav_module

        edge_msg = EdgeNavResultMessage(
            frame_id="f_3",
            labels=[],
            timestamp_ms=nav_module._now_ms() + 60_000,  # 未来 60s
        )
        started = time.monotonic() - 0.02
        with patch(
            "app.observability.metrics_collector.get_metrics_collector",
            lambda: collector,
        ):
            seconds = record_nav_latency(edge_msg, started)

        assert seconds is not None
        assert seconds < 0.2  # 已回退为后端耗时

    def test_metric_failure_never_breaks_navigation(self):
        with patch(
            "app.observability.metrics_collector.get_metrics_collector",
            MagicMock(side_effect=RuntimeError("registry down")),
        ):
            frame_msg = FrameMessage(
                type=WebSocketMessageType.FRAME,
                timestamp=int(time.time()),
                frame_id="f_4",
                frame_index=4,
            )
            assert record_nav_latency(frame_msg, time.monotonic()) is None


# ----------------------------------------------------------------------
# 跳 3：后端 → 前端投递
# ----------------------------------------------------------------------


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: Dict[str, Any]) -> None:
        self.sent.append(payload)


class TestEdgeNavResultIngestion:
    """边缘端上行 ``nav_result`` 的后端消费（此前完全没有消费者）。"""

    def _env(self):
        manager = MagicMock()
        manager.get_patient_id = AsyncMock(return_value="P001")
        manager.send_personal_message = AsyncMock()
        manager.send_navigation_revocation = AsyncMock()
        return manager

    def test_labels_convert_to_normalized_annotations(self):
        edge_msg = EdgeNavResultMessage(
            frame_id="f_edge",
            session_id="sess_nav",
            latency_ms=4.2,
            backend="tensorrt",
            labels=[
                EdgeNavLabel(
                    label="organ_name", x=0.3, y=0.25, width=0.2, height=0.18, confidence=0.92
                )
            ],
            timestamp_ms=1700000000000,
        )
        annotations = edge_labels_to_annotations(edge_msg.labels)
        assert len(annotations) == 1
        assert annotations[0].label == "organ_name"
        # 归一化坐标不得被取整（bbox 曾为 List[int]）
        assert annotations[0].bbox == [0.3, 0.25, 0.2, 0.18]

    @pytest.mark.asyncio
    async def test_edge_nav_result_delivered_to_frontend(self):
        manager = self._env()
        sg_client = MagicMock()
        sg_client.check_output = AsyncMock(return_value={"allowed": True})

        edge_msg = EdgeNavResultMessage(
            frame_id="f_edge",
            session_id="sess_nav",
            backend="tensorrt",
            labels=[
                EdgeNavLabel(
                    label="boundary", x=0.1, y=0.2, width=0.3, height=0.1, confidence=0.8
                )
            ],
            timestamp_ms=1700000000000,
        )

        with patch("app.api.websocket.nav.safety_gateway_client", sg_client), patch(
            "app.api.websocket.nav.ConsentRepository"
        ) as consent_cls, patch(
            "app.api.websocket.nav.AsyncSessionLocal"
        ) as session_factory:
            consent_cls.return_value.get_latest_consent = AsyncMock(
                return_value=MagicMock(status="granted")
            )
            session_factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            delivered = await deliver_navigation_annotations(
                manager=manager,
                session_id="sess_nav",
                frame_id=edge_msg.frame_id,
                annotations=edge_labels_to_annotations(edge_msg.labels),
                message=edge_msg,
                started_monotonic=time.monotonic(),
            )

        assert delivered is True
        manager.send_personal_message.assert_awaited_once()
        payload = manager.send_personal_message.await_args.args[0]
        assert payload["type"] == WebSocketMessageType.NAVIGATION.value
        assert payload["frame_id"] == "f_edge"
        assert payload["annotations"][0]["label"] == "boundary"
        # 安全网关确实收到了边缘端标注
        assert sg_client.check_output.await_args.kwargs["output_data"]["annotations"][0][
            "label"
        ] == "boundary"

    @pytest.mark.asyncio
    async def test_safety_gateway_denial_revokes_navigation(self):
        manager = self._env()
        sg_client = MagicMock()
        sg_client.check_output = AsyncMock(
            return_value={"allowed": False, "message": "consent withdrawn"}
        )

        edge_msg = EdgeNavResultMessage(frame_id="f_denied", session_id="sess_nav", labels=[])

        with patch("app.api.websocket.nav.safety_gateway_client", sg_client), patch(
            "app.api.websocket.nav.ConsentRepository"
        ) as consent_cls, patch(
            "app.api.websocket.nav.AsyncSessionLocal"
        ) as session_factory:
            consent_cls.return_value.get_latest_consent = AsyncMock(return_value=None)
            session_factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            delivered = await deliver_navigation_annotations(
                manager=manager,
                session_id="sess_nav",
                frame_id=edge_msg.frame_id,
                annotations=[],
                message=edge_msg,
                started_monotonic=time.monotonic(),
            )

        assert delivered is False
        manager.send_personal_message.assert_not_awaited()
        manager.send_navigation_revocation.assert_awaited_once()
        assert (
            manager.send_navigation_revocation.await_args.kwargs["reason"]
            == "consent withdrawn"
        )

    @pytest.mark.asyncio
    async def test_safety_gateway_failure_degrades_not_blocks(self):
        """SG 不可用时按 DEGRADED 下发（导航不因安全网关故障而中断）。"""
        manager = self._env()
        sg_client = MagicMock()
        sg_client.check_output = AsyncMock(side_effect=RuntimeError("sg down"))

        edge_msg = EdgeNavResultMessage(
            frame_id="f_degraded",
            session_id="sess_nav",
            labels=[
                EdgeNavLabel(
                    label="organ_name", x=0.1, y=0.1, width=0.1, height=0.1, confidence=0.9
                )
            ],
        )

        with patch("app.api.websocket.nav.safety_gateway_client", sg_client), patch(
            "app.api.websocket.nav.ConsentRepository"
        ) as consent_cls, patch(
            "app.api.websocket.nav.AsyncSessionLocal"
        ) as session_factory:
            consent_cls.return_value.get_latest_consent = AsyncMock(return_value=None)
            session_factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            delivered = await deliver_navigation_annotations(
                manager=manager,
                session_id="sess_nav",
                frame_id=edge_msg.frame_id,
                annotations=edge_labels_to_annotations(edge_msg.labels),
                message=edge_msg,
                started_monotonic=time.monotonic(),
            )

        assert delivered is True
        payload = manager.send_personal_message.await_args.args[0]
        assert payload["desensitization_status"] == "degraded"


class TestBackendToFrontend:
    @pytest.mark.asyncio
    async def test_navigation_message_delivered_to_client(self):
        from app.api.websocket.nav import ConnectionManager

        manager = ConnectionManager()
        ws = _FakeWebSocket()
        await manager.connect(ws, "sess_nav", "dev_1")

        message = {
            "type": WebSocketMessageType.NAVIGATION.value,
            "frame_id": "f_1",
            "annotations": [
                {"label": "thyroid", "confidence": 0.92, "bbox": [0.3, 0.25, 0.2, 0.18]}
            ],
            "desensitization_status": "full",
        }
        await manager.send_personal_message(message, "sess_nav")

        assert ws.accepted is True
        assert ws.sent and ws.sent[0]["frame_id"] == "f_1"
        assert ws.sent[0]["annotations"][0]["label"] == "thyroid"

    @pytest.mark.asyncio
    async def test_disconnected_client_receives_nothing(self):
        from app.api.websocket.nav import ConnectionManager

        manager = ConnectionManager()
        ws = _FakeWebSocket()
        await manager.connect(ws, "sess_nav", "dev_1")
        await manager.disconnect("sess_nav", "dev_1")

        await manager.send_personal_message({"type": "navigation"}, "sess_nav")
        assert ws.sent == []
