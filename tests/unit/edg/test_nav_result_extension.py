"""M1/M2/M6 边缘端集成：导航推理服务产出 + 上行载荷序列化。

覆盖"适配器 → NavResult → 上行 JSON"这条链路，重点是不破坏旧后端：
新增字段只在确有值时出现。
"""
import json

import numpy as np
import pytest

from edge.device_auth import EdgeDeviceAuth
from edge.image_quality import LocalFakeImageQualityAdapter
from edge.navigation import NavigationInferenceService
from edge.plane_assessment import LocalFakePlaneAssessmentAdapter
from edge.protocols import Frame, NavLabel, NavResult
from edge.websocket_server import EdgeWebSocketClient

pytestmark = pytest.mark.track_edg


class _FakeConnection:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send(self, message):
        self.sent.append(message)

    async def close(self):
        self.closed = True


class _FakeBackend:
    name = "fake-trt"

    def __init__(self, labels):
        self._labels = labels

    def load(self):
        pass

    def infer(self, frame):
        return list(self._labels)

    def close(self):
        pass


def _frame(shape=(200, 150)) -> Frame:
    rng = np.random.RandomState(3)
    pixels = (rng.rand(*shape) * 200 + 30).astype(np.uint8)
    return Frame(
        frame_id="f1",
        session_id="sess_001",
        index=0,
        pixels=pixels,
        received_at_ms=0.0,
    )


def _service(labels, **kwargs) -> NavigationInferenceService:
    return NavigationInferenceService(
        _FakeBackend(labels), target_fps=1000.0, organ_code="thyroid", **kwargs
    )


def _auth():
    return EdgeDeviceAuth(device_id="dev_edge_001", api_key="edge-api-key-0123456789abcdef")


class TestServiceProducesFrameAttributes:
    def test_adapters_populate_nav_result(self):
        service = _service(
            [NavLabel(label="organ_name", x=0.1, y=0.1, width=0.2, height=0.2, confidence=0.9)],
            plane_assessment_adapter=LocalFakePlaneAssessmentAdapter(),
            image_quality_adapter=LocalFakeImageQualityAdapter(),
        )
        result = service.process_frame(_frame())
        assert result.plane_assessment is not None
        assert result.plane_assessment.organ_code == "thyroid"
        assert result.image_quality is not None
        assert 0.0 <= result.image_quality.overall_score <= 1.0

    def test_default_adapters_produce_nothing(self):
        service = _service(
            [NavLabel(label="organ_name", x=0.1, y=0.1, width=0.2, height=0.2, confidence=0.9)]
        )
        result = service.process_frame(_frame())
        assert result.plane_assessment is None
        assert result.image_quality is None

    def test_risk_marking_applied_by_default(self):
        service = _service(
            [
                NavLabel(
                    label="malignant_suspected",
                    x=0.2,
                    y=0.2,
                    width=0.2,
                    height=0.2,
                    confidence=0.95,
                )
            ]
        )
        result = service.process_frame(_frame())
        assert result.labels[0].risk_flag == "high"
        assert result.labels[0].suggested_action == "start_standard_evaluation"
        assert service.stats()["risk_marked_labels"] == 1

    def test_risk_marking_can_be_disabled(self):
        service = _service(
            [
                NavLabel(
                    label="malignant_suspected",
                    x=0.2,
                    y=0.2,
                    width=0.2,
                    height=0.2,
                    confidence=0.95,
                )
            ],
            enable_risk_marking=False,
        )
        result = service.process_frame(_frame())
        assert result.labels[0].risk_flag is None
        assert service.stats()["risk_marked_labels"] == 0

    def test_adapter_failure_does_not_break_navigation(self):
        class _BrokenAdapter:
            name = "broken"

            def load(self):
                pass

            def assess(self, frame, organ_code=None):
                raise RuntimeError("gpu oom")

            def close(self):
                pass

        service = _service(
            [NavLabel(label="organ_name", x=0.1, y=0.1, width=0.2, height=0.2, confidence=0.9)],
            plane_assessment_adapter=_BrokenAdapter(),
        )
        result = service.process_frame(_frame())
        assert result.plane_assessment is None
        assert [label.label for label in result.labels] == ["organ_name"]

    def test_stats_report_adapter_backends(self):
        service = _service(
            [],
            plane_assessment_adapter=LocalFakePlaneAssessmentAdapter(),
            image_quality_adapter=LocalFakeImageQualityAdapter(),
        )
        stats = service.stats()
        assert stats["plane_assessment_backend"] == "local_fake"
        assert stats["image_quality_backend"] == "local_fake"


class TestUplinkPayload:
    @pytest.mark.asyncio
    async def test_payload_includes_frame_attributes_and_risk(self):
        connection = _FakeConnection()

        async def _factory(url, headers):
            return connection

        client = EdgeWebSocketClient("ws://backend:8000", _auth(), connect_factory=_factory)
        service = _service(
            [
                NavLabel(
                    label="malignant_suspected",
                    x=0.2,
                    y=0.2,
                    width=0.2,
                    height=0.2,
                    confidence=0.95,
                )
            ],
            plane_assessment_adapter=LocalFakePlaneAssessmentAdapter(),
            image_quality_adapter=LocalFakeImageQualityAdapter(),
        )
        result = service.process_frame(_frame())
        assert await client.connect() is True
        assert await client.push_nav_result(result) is True

        payload = json.loads(connection.sent[0])
        assert payload["type"] == "nav_result"
        assert payload["labels"][0]["risk_flag"] == "high"
        assert payload["labels"][0]["suggested_action"] == "start_standard_evaluation"
        assert payload["plane_assessment"]["plane_type"] in (
            "transverse",
            "longitudinal",
            "non_standard",
        )
        assert payload["image_quality"]["overall_score"] is not None

    @pytest.mark.asyncio
    async def test_payload_omits_new_keys_when_absent(self):
        """未部署评估头且无高危标记时，载荷与改造前一致（旧后端可照旧解析）。"""
        connection = _FakeConnection()

        async def _factory(url, headers):
            return connection

        client = EdgeWebSocketClient("ws://backend:8000", _auth(), connect_factory=_factory)
        legacy = NavResult(
            frame_id="f0",
            session_id="sess_001",
            labels=[
                NavLabel(
                    label="organ_name", x=0.1, y=0.1, width=0.2, height=0.2, confidence=0.9
                )
            ],
            infer_latency_ms=12.5,
            backend="fake-trt",
        )
        assert await client.connect() is True
        assert await client.push_nav_result(legacy) is True

        payload = json.loads(connection.sent[0])
        assert "plane_assessment" not in payload
        assert "image_quality" not in payload
        assert "risk_flag" not in payload["labels"][0]
        assert "suggested_action" not in payload["labels"][0]
        assert set(payload["labels"][0].keys()) == {
            "label",
            "x",
            "y",
            "width",
            "height",
            "confidence",
        }
