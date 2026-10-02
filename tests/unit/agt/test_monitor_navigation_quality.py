"""M1/M6 切面合格性与图像质量监控单测（流程监控智能体）。"""
import pytest

from app.agents.monitor import (
    EXPECTED_STRUCTURES_BY_ORGAN,
    TAG_IMAGE_QUALITY_POOR_STREAK,
    TAG_PLANE_KEY_STRUCTURE_MISSING,
    TAG_PLANE_NON_STANDARD_STREAK,
    MonitorAgentImpl,
)
from app.models.schemas import (
    ImageQuality,
    ImageQualityImpact,
    PlaneAssessment,
    PlaneType,
)

pytestmark = pytest.mark.track_agt


@pytest.fixture
def monitor() -> MonitorAgentImpl:
    return MonitorAgentImpl()


def _plane(
    *,
    organ="thyroid",
    is_standard=True,
    plane_type=PlaneType.LONGITUDINAL,
    visible=None,
):
    if visible is None:
        visible = list(EXPECTED_STRUCTURES_BY_ORGAN["thyroid"]) if is_standard else []
    return PlaneAssessment(
        plane_type=plane_type,
        is_standard=is_standard,
        plane_score=0.9 if is_standard else 0.3,
        organ_code=organ,
        issues=[] if is_standard else ["plane_below_standard"],
        required_structures_visible=visible,
    )


def _quality(overall=0.8):
    return ImageQuality(
        overall_score=overall,
        diagnostic_impact=(
            ImageQualityImpact.NONE if overall >= 0.7 else ImageQualityImpact.MAJOR
        ),
    )


class TestPlaneStreak:
    def test_alerts_exactly_at_threshold(self, monitor):
        threshold = monitor._plane_streak_threshold
        tags = []
        for index in range(threshold):
            tags = monitor.observe_navigation_frame(
                session_id="s1",
                frame_id=f"f{index}",
                plane_assessment=_plane(is_standard=False, visible=["left_lobe", "right_lobe"]),
            )
        assert TAG_PLANE_NON_STANDARD_STREAK in tags

    def test_does_not_alert_before_threshold(self, monitor):
        threshold = monitor._plane_streak_threshold
        for index in range(threshold - 1):
            tags = monitor.observe_navigation_frame(
                session_id="s1",
                frame_id=f"f{index}",
                plane_assessment=_plane(is_standard=False, visible=["left_lobe", "right_lobe"]),
            )
            assert TAG_PLANE_NON_STANDARD_STREAK not in tags

    def test_edge_triggered_not_level_triggered(self, monitor):
        """达到阈值后继续非标准不应逐帧重复告警（否则 15fps 刷爆日志）。"""
        threshold = monitor._plane_streak_threshold
        for index in range(threshold):
            monitor.observe_navigation_frame(
                session_id="s1",
                frame_id=f"a{index}",
                plane_assessment=_plane(is_standard=False, visible=["left_lobe", "right_lobe"]),
            )
        for index in range(threshold, threshold + 3):
            tags = monitor.observe_navigation_frame(
                session_id="s1",
                frame_id=f"b{index}",
                plane_assessment=_plane(is_standard=False, visible=["left_lobe", "right_lobe"]),
            )
            assert TAG_PLANE_NON_STANDARD_STREAK not in tags

    def test_standard_frame_resets_streak(self, monitor):
        threshold = monitor._plane_streak_threshold
        for index in range(threshold - 1):
            monitor.observe_navigation_frame(
                session_id="s1", frame_id=f"f{index}", plane_assessment=_plane(is_standard=False)
            )
        monitor.observe_navigation_frame(
            session_id="s1", frame_id="ok", plane_assessment=_plane(is_standard=True)
        )
        assert monitor.get_navigation_state("s1").non_standard_streak == 0
        tags = monitor.observe_navigation_frame(
            session_id="s1", frame_id="again", plane_assessment=_plane(is_standard=False)
        )
        assert TAG_PLANE_NON_STANDARD_STREAK not in tags

    def test_sessions_are_isolated(self, monitor):
        threshold = monitor._plane_streak_threshold
        for index in range(threshold):
            monitor.observe_navigation_frame(
                session_id="s1", frame_id=f"f{index}", plane_assessment=_plane(is_standard=False)
            )
        tags = monitor.observe_navigation_frame(
            session_id="s2", frame_id="f0", plane_assessment=_plane(is_standard=False)
        )
        assert TAG_PLANE_NON_STANDARD_STREAK not in tags


class TestKeyStructureMissing:
    def test_alerts_on_first_missing_report(self, monitor):
        tags = monitor.observe_navigation_frame(
            session_id="s1",
            frame_id="f1",
            plane_assessment=_plane(is_standard=False, visible=["left_lobe", "right_lobe"]),
        )
        assert TAG_PLANE_KEY_STRUCTURE_MISSING in tags

    def test_no_alert_when_all_structures_visible(self, monitor):
        tags = monitor.observe_navigation_frame(
            session_id="s1", frame_id="f1", plane_assessment=_plane(is_standard=True)
        )
        assert TAG_PLANE_KEY_STRUCTURE_MISSING not in tags

    def test_same_missing_set_does_not_repeat(self, monitor):
        missing_visible = ["left_lobe", "right_lobe"]
        first = monitor.observe_navigation_frame(
            session_id="s1",
            frame_id="f1",
            plane_assessment=_plane(is_standard=False, visible=missing_visible),
        )
        second = monitor.observe_navigation_frame(
            session_id="s1",
            frame_id="f2",
            plane_assessment=_plane(is_standard=False, visible=missing_visible),
        )
        assert TAG_PLANE_KEY_STRUCTURE_MISSING in first
        assert TAG_PLANE_KEY_STRUCTURE_MISSING not in second

    def test_recovery_clears_missing_record(self, monitor):
        monitor.observe_navigation_frame(
            session_id="s1",
            frame_id="f1",
            plane_assessment=_plane(is_standard=False, visible=["left_lobe", "right_lobe"]),
        )
        monitor.observe_navigation_frame(
            session_id="s1", frame_id="f2", plane_assessment=_plane(is_standard=True)
        )
        assert monitor.get_navigation_state("s1").missing_structures == []

    def test_no_inference_when_adapter_reports_nothing(self, monitor):
        """适配器未提供结构信息（空列表）时不得臆断缺失——否则未接真机会全量误报。"""
        tags = monitor.observe_navigation_frame(
            session_id="s1", frame_id="f1", plane_assessment=_plane(visible=[])
        )
        assert TAG_PLANE_KEY_STRUCTURE_MISSING not in tags

    def test_unknown_organ_has_no_expectation(self, monitor):
        tags = monitor.observe_navigation_frame(
            session_id="s1",
            frame_id="f1",
            plane_assessment=_plane(organ="unknown_organ", visible=["whatever"]),
        )
        assert TAG_PLANE_KEY_STRUCTURE_MISSING not in tags


class TestImageQualityStreak:
    def test_alerts_at_threshold(self, monitor):
        threshold = monitor._quality_streak_threshold
        tags = []
        for index in range(threshold):
            tags = monitor.observe_navigation_frame(
                session_id="s1", frame_id=f"f{index}", image_quality=_quality(0.2)
            )
        assert TAG_IMAGE_QUALITY_POOR_STREAK in tags

    def test_good_frame_resets_streak(self, monitor):
        threshold = monitor._quality_streak_threshold
        for index in range(threshold):
            monitor.observe_navigation_frame(
                session_id="s1", frame_id=f"a{index}", image_quality=_quality(0.2)
            )
        monitor.observe_navigation_frame(
            session_id="s1", frame_id="good", image_quality=_quality(0.9)
        )
        assert monitor.get_navigation_state("s1").poor_quality_streak == 0

    def test_edge_triggered(self, monitor):
        threshold = monitor._quality_streak_threshold
        for index in range(threshold):
            monitor.observe_navigation_frame(
                session_id="s1", frame_id=f"a{index}", image_quality=_quality(0.2)
            )
        tags = monitor.observe_navigation_frame(
            session_id="s1", frame_id="more", image_quality=_quality(0.2)
        )
        assert TAG_IMAGE_QUALITY_POOR_STREAK not in tags


class TestObservationRobustness:
    def test_returns_empty_without_session(self, monitor):
        assert monitor.observe_navigation_frame(session_id="", plane_assessment=_plane()) == []

    def test_returns_empty_without_payload(self, monitor):
        assert monitor.observe_navigation_frame(session_id="s1") == []

    def test_tolerates_duck_typed_payloads(self, monitor):
        from types import SimpleNamespace

        tags = monitor.observe_navigation_frame(
            session_id="s1",
            frame_id="f1",
            plane_assessment=SimpleNamespace(
                is_standard=False,
                plane_type="non_standard",
                issues=["image_too_dark"],
                organ_code="thyroid",
                required_structures_visible=["left_lobe", "right_lobe"],
            ),
            image_quality=SimpleNamespace(overall_score=0.1, issues=["severe_blur"]),
        )
        assert TAG_PLANE_KEY_STRUCTURE_MISSING in tags

    def test_reset_navigation_state(self, monitor):
        monitor.observe_navigation_frame(
            session_id="s1", frame_id="f1", plane_assessment=_plane(is_standard=False)
        )
        monitor.observe_navigation_frame(
            session_id="s2", frame_id="f1", plane_assessment=_plane(is_standard=False)
        )
        monitor.reset_navigation_state("s1")
        assert monitor.get_navigation_state("s1") is None
        assert monitor.get_navigation_state("s2") is not None
        monitor.reset_navigation_state()
        assert monitor.get_navigation_state("s2") is None

    @pytest.mark.asyncio
    async def test_close_clears_navigation_state(self, monitor):
        monitor.observe_navigation_frame(
            session_id="s1", frame_id="f1", plane_assessment=_plane(is_standard=False)
        )
        await monitor.close()
        assert monitor.get_navigation_state("s1") is None
