"""M2 导航链路接线单测：标注转换 + 热路径上的升级调度。

这里要守住两条硬约束：
1. **契约净化**：只有 ``high`` / ``start_standard_evaluation`` 能进入对外标注，
   其它字符串一律丢弃；
2. **热路径不许阻塞**：自动升级必须派生后台任务，且防抖（同步预留）必须在
   派生任务之前完成，否则 15fps 的导航流会派生出一堆任务并打满诊断队列。
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.api.websocket import nav as nav_module
from app.api.websocket.nav import _schedule_auto_upgrade, edge_labels_to_annotations
from app.models.schemas import EdgeNavLabel, RiskFlag, SuggestedAction
from app.services.navigation_upgrade import NavigationUpgradeService

pytestmark = pytest.mark.track_api


def _edge_label(label="nodule", risk=None, suggested=None, confidence=0.9):
    kwargs = {}
    if risk is not None:
        kwargs["risk_flag"] = risk
    if suggested is not None:
        kwargs["suggested_action"] = suggested
    return EdgeNavLabel(
        label=label, x=0.1, y=0.2, width=0.3, height=0.4, confidence=confidence, **kwargs
    )


class TestEdgeLabelsToAnnotations:
    def test_plain_label_has_no_risk_fields(self):
        (annotation,) = edge_labels_to_annotations([_edge_label()])
        assert annotation.risk_flag is None
        assert annotation.suggested_action is None
        assert annotation.bbox == [0.1, 0.2, 0.3, 0.4]
        assert annotation.confidence == 0.9

    def test_bplus_marking_is_carried_through(self):
        (annotation,) = edge_labels_to_annotations(
            [_edge_label("malignant_suspected", risk="high", suggested="start_standard_evaluation")]
        )
        assert annotation.risk_flag == RiskFlag.HIGH
        assert annotation.suggested_action == SuggestedAction.START_STANDARD_EVALUATION
        dumped = annotation.model_dump()
        assert dumped["risk_flag"] == "high"
        assert dumped["suggested_action"] == "start_standard_evaluation"

    @pytest.mark.parametrize("risk", ["medium", "HIGH", "", "yes", "1"])
    def test_invalid_risk_flag_is_dropped(self, risk):
        (annotation,) = edge_labels_to_annotations([_edge_label(risk=risk)])
        assert annotation.risk_flag is None

    def test_invalid_suggested_action_dropped_but_valid_flag_kept(self):
        (annotation,) = edge_labels_to_annotations(
            [_edge_label(risk="high", suggested="do_something_else")]
        )
        assert annotation.risk_flag == RiskFlag.HIGH
        assert annotation.suggested_action is None

    def test_action_without_flag_keeps_flag_none(self):
        """只带动作不带标记时仍不应产生 B+ 级（标记是触发源）。"""
        (annotation,) = edge_labels_to_annotations(
            [_edge_label(suggested="start_standard_evaluation")]
        )
        assert annotation.risk_flag is None
        assert annotation.suggested_action == SuggestedAction.START_STANDARD_EVALUATION

    def test_multiple_labels_preserve_order(self):
        annotations = edge_labels_to_annotations(
            [_edge_label("a", risk="high"), _edge_label("b"), _edge_label("c", risk="high")]
        )
        assert [a.label for a in annotations] == ["a", "b", "c"]
        assert [a.risk_flag for a in annotations] == [RiskFlag.HIGH, None, RiskFlag.HIGH]


class TestScheduleAutoUpgrade:
    @pytest.fixture(autouse=True)
    def _restore_module_service(self, monkeypatch):
        # 每个用例都换成独立实例，避免单例状态串味
        self.service = NavigationUpgradeService(
            cooldown_seconds=60.0, enqueue=AsyncMock(return_value=True)
        )
        monkeypatch.setattr(
            nav_module, "get_navigation_upgrade_service", lambda: self.service
        )
        # 不打真实 DB：把 perform_upgrade 换成受控桩
        self.performed: list = []

        async def _fake_perform(**kwargs):
            self.performed.append(kwargs)
            from app.services.navigation_upgrade import UpgradeDecision

            return UpgradeDecision(True, "b_plus_risk_flag", request_id="req_stub")

        monkeypatch.setattr(self.service, "perform_upgrade", _fake_perform)
        yield

    @pytest.mark.asyncio
    async def test_disabled_by_settings_returns_none(self, monkeypatch):
        monkeypatch.setattr(nav_module.settings, "nav_auto_upgrade_enabled", False)
        annotations = edge_labels_to_annotations([_edge_label(risk="high")])
        assert (
            _schedule_auto_upgrade(
                session_id="s1", frame_id="f1", annotations=annotations, patient_id="p1"
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_no_risk_flag_returns_none(self, monkeypatch):
        monkeypatch.setattr(nav_module.settings, "nav_auto_upgrade_enabled", True)
        annotations = edge_labels_to_annotations([_edge_label()])
        assert (
            _schedule_auto_upgrade(
                session_id="s1", frame_id="f1", annotations=annotations, patient_id="p1"
            )
            is None
        )
        assert self.service.upgrade_count("s1") == 0

    @pytest.mark.asyncio
    async def test_schedules_background_task(self, monkeypatch):
        monkeypatch.setattr(nav_module.settings, "nav_auto_upgrade_enabled", True)
        annotations = edge_labels_to_annotations([_edge_label(risk="high")])
        task = _schedule_auto_upgrade(
            session_id="s1", frame_id="f1", annotations=annotations, patient_id="p1"
        )
        assert task is not None
        await task
        # 配额在派生任务**之前**就已占用
        assert self.service.upgrade_count("s1") == 1
        assert len(self.performed) == 1
        assert self.performed[0]["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_cooldown_prevents_task_spam(self, monkeypatch):
        """15fps 的连续高危帧只能产生一个任务（防抖在同步阶段生效）。"""
        monkeypatch.setattr(nav_module.settings, "nav_auto_upgrade_enabled", True)
        annotations = edge_labels_to_annotations([_edge_label(risk="high")])
        first = _schedule_auto_upgrade(
            session_id="s1", frame_id="f1", annotations=annotations, patient_id="p1"
        )
        assert first is not None
        for index in range(2, 30):
            assert (
                _schedule_auto_upgrade(
                    session_id="s1",
                    frame_id=f"f{index}",
                    annotations=annotations,
                    patient_id="p1",
                )
                is None
            )
        await first
        assert len(self.performed) == 1

    @pytest.mark.asyncio
    async def test_task_reference_is_released_after_completion(self, monkeypatch):
        monkeypatch.setattr(nav_module.settings, "nav_auto_upgrade_enabled", True)
        annotations = edge_labels_to_annotations([_edge_label(risk="high")])
        task = _schedule_auto_upgrade(
            session_id="s9", frame_id="f1", annotations=annotations, patient_id="p1"
        )
        await task
        # done_callback 会在下一轮事件循环中移除引用
        await asyncio.sleep(0)
        assert task not in nav_module._pending_upgrade_tasks
