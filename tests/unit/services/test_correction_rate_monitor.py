"""AI 修正率监控（二期 §7）。

重点：
1. 方向判定要能识别分级值（TR4 -> TR3 是"偏高"），非分级表述必须**单独报数**而不是
   静默丢弃——丢弃会让修正率分母失真，看板上看不出还有一批反馈没被纳入；
2. 样本不足时只统计不判定：1/1=100% 不该触发个体告警；
3. 方向占比以"可分级条数"为分母，否则非分级表述会把方向占比稀释掉，明明全是偏高
   也测不出越线；
4. 个体偏离必须与同龄群体比较，只有一位医生有足够样本时不构成"偏离"。
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.config.settings import settings
from app.services.correction_rate_monitor import (
    CorrectionRateMonitor,
    CorrectionRecord,
    GradeDirection,
    classify_direction,
)


def _record(reviewer="doc_1", original="TR4", corrected="TR3", field_name="diagnosis"):
    return CorrectionRecord(
        reviewer_id=reviewer,
        field=field_name,
        original_value=original,
        corrected_value=corrected,
    )


def _considered(count, reviewer=None):
    return [
        SimpleNamespace(reviewer_id=reviewer, request_id=f"req_{index}")
        for index in range(count)
    ]


class TestDirectionClassification:
    def test_ai_higher(self):
        assert classify_direction("TR4", "TR3") == GradeDirection.AI_HIGHER

    def test_ai_lower(self):
        assert classify_direction("TR3", "TR4") == GradeDirection.AI_LOWER

    def test_same_level(self):
        assert classify_direction("TR4", "TR4") == GradeDirection.SAME

    def test_prefixed_codes(self):
        assert classify_direction("LIRADS-5", "LIRADS-4") == GradeDirection.AI_HIGHER
        assert classify_direction("grade 3", "grade 4") == GradeDirection.AI_LOWER

    def test_non_gradable_text(self):
        assert classify_direction("良性", "恶性") == GradeDirection.UNGRADABLE

    def test_mixed_gradable_and_text(self):
        assert classify_direction("TR4", "考虑恶性") == GradeDirection.UNGRADABLE


class TestRecordAdapter:
    def test_builds_from_correction_row(self):
        row = SimpleNamespace(
            feedback_type="correction",
            reviewer_id="doc_9",
            content={
                "field": "diagnosis",
                "original_value": "TR4",
                "corrected_value": "TR3",
                "reason": "边界更光整",
            },
        )

        record = CorrectionRecord.from_feedback_row(row)

        assert record is not None
        assert record.reviewer_id == "doc_9"
        assert record.original_value == "TR4"

    def test_ignores_non_correction_feedback(self):
        row = SimpleNamespace(feedback_type="supplement", reviewer_id="d", content={})

        assert CorrectionRecord.from_feedback_row(row) is None

    def test_ignores_incomplete_payload(self):
        row = SimpleNamespace(
            feedback_type="correction",
            reviewer_id="d",
            content={"field": "diagnosis", "original_value": "TR4"},
        )

        assert CorrectionRecord.from_feedback_row(row) is None

    def test_dirty_row_does_not_break_batch(self):
        class _Exploding:
            @property
            def feedback_type(self):
                raise RuntimeError("corrupt")

        assert CorrectionRecord.from_feedback_row(_Exploding()) is None


class TestAggregation:
    def test_counts_and_rate(self):
        monitor = CorrectionRateMonitor(
            considered_records=_considered(100),
            correction_records=[_record() for _ in range(10)],
        )

        report = monitor.assess()

        assert report.considered == 100
        assert report.corrected == 10
        assert report.correction_rate == pytest.approx(0.1)

    def test_ungradable_reported_separately(self):
        monitor = CorrectionRateMonitor(
            considered_records=_considered(100),
            correction_records=[
                _record(original="TR4", corrected="TR3"),
                _record(original="甲状腺结节", corrected="良性结节"),
            ],
        )

        report = monitor.assess()

        assert report.direction_counts[GradeDirection.UNGRADABLE.value] == 1
        assert report.corrected == 2, "无法判定方向也要计入修正率分母"

    def test_direction_rate_uses_gradable_denominator(self):
        """全是偏高时方向占比必须是 100%，不能被非分级表述稀释。"""
        monitor = CorrectionRateMonitor(
            considered_records=_considered(100),
            correction_records=[
                _record(original="TR4", corrected="TR3"),
                _record(original="TR4", corrected="TR3"),
                _record(original="甲状腺结节", corrected="良性结节"),
            ],
        )

        report = monitor.assess()

        assert report.direction_rate(GradeDirection.AI_HIGHER) == pytest.approx(1.0)

    def test_direction_rate_zero_without_gradable(self):
        monitor = CorrectionRateMonitor(
            considered_records=_considered(100),
            correction_records=[_record(original="甲", corrected="乙")],
        )

        report = monitor.assess()

        assert report.direction_rate(GradeDirection.AI_HIGHER) == 0.0


class TestDirectionalAlert:
    def test_alerts_when_single_direction_exceeds_threshold(self, monkeypatch):
        monkeypatch.setattr(settings, "correction_rate_min_samples", 10)
        monkeypatch.setattr(settings, "correction_rate_direction_alert_threshold", 0.20)
        # 10 条修正里 8 条偏高、2 条偏低 -> 偏高占 80%
        records = [_record(original="TR4", corrected="TR3") for _ in range(8)] + [
            _record(original="TR3", corrected="TR4") for _ in range(2)
        ]

        report = CorrectionRateMonitor(
            considered_records=_considered(100), correction_records=records
        ).assess()

        assert report.directional_bias_alert is not None
        assert report.directional_bias_alert["direction"] == GradeDirection.AI_HIGHER.value
        assert "偏高" in report.directional_bias_alert["message"]

    def test_balanced_directions_do_not_alert(self, monkeypatch):
        monkeypatch.setattr(settings, "correction_rate_min_samples", 10)
        monkeypatch.setattr(settings, "correction_rate_direction_alert_threshold", 0.20)
        records = [_record(original="TR4", corrected="TR3") for _ in range(5)] + [
            _record(original="TR3", corrected="TR4") for _ in range(5)
        ]

        report = CorrectionRateMonitor(
            considered_records=_considered(100), correction_records=records
        ).assess()

        assert report.directional_bias_alert is None

    def test_insufficient_samples_suppresses_direction_alert(self, monkeypatch):
        """样本太少时不判定——否则一次修正就能报出 100% 系统性偏高。"""
        monkeypatch.setattr(settings, "correction_rate_min_samples", 50)

        report = CorrectionRateMonitor(
            considered_records=_considered(10),
            correction_records=[_record(original="TR4", corrected="TR3")],
        ).assess()

        assert report.insufficient_samples is True
        assert report.directional_bias_alert is None
        assert report.correction_rate == pytest.approx(0.1), "统计仍要如实给出"


class TestReviewerOutlier:
    def _monitor(self, monkeypatch):
        monkeypatch.setattr(settings, "correction_rate_min_samples", 10)
        monkeypatch.setattr(settings, "correction_rate_reviewer_alert_threshold", 0.50)
        monkeypatch.setattr(settings, "correction_rate_peer_baseline_threshold", 0.20)
        considered = _considered(50, reviewer="doc_high") + _considered(
            50, reviewer="doc_peer"
        )
        records = [_record(reviewer="doc_high") for _ in range(40)] + [
            _record(reviewer="doc_peer") for _ in range(5)
        ]
        return CorrectionRateMonitor(
            considered_records=considered, correction_records=records
        )

    def test_flags_outlier_reviewer(self, monkeypatch):
        report = self._monitor(monkeypatch).assess()

        assert len(report.reviewer_outlier_alerts) == 1
        alert = report.reviewer_outlier_alerts[0]
        assert alert["reviewer_id"] == "doc_high"
        assert alert["correction_rate"] == pytest.approx(0.8)
        assert alert["peer_rate"] == pytest.approx(0.1)

    def test_single_reviewer_is_not_an_outlier(self, monkeypatch):
        """只有一位医生有足够样本时，"偏离群体"无从谈起。"""
        monkeypatch.setattr(settings, "correction_rate_min_samples", 10)
        monkeypatch.setattr(settings, "correction_rate_reviewer_alert_threshold", 0.50)
        monitor = CorrectionRateMonitor(
            considered_records=_considered(50, reviewer="doc_solo"),
            correction_records=[_record(reviewer="doc_solo") for _ in range(45)],
        )

        report = monitor.assess()

        assert report.reviewer_outlier_alerts == []

    def test_reviewer_without_corrections_is_still_a_peer(self, monkeypatch):
        """一次修正都没有的医生也必须进入统计：否则"群体"凭空少人，个体偏离测不出。"""
        monkeypatch.setattr(settings, "correction_rate_min_samples", 10)
        monkeypatch.setattr(settings, "correction_rate_reviewer_alert_threshold", 0.50)
        monkeypatch.setattr(settings, "correction_rate_peer_baseline_threshold", 0.20)
        considered = _considered(50, reviewer="doc_high") + _considered(
            50, reviewer="doc_clean"
        )

        report = CorrectionRateMonitor(
            considered_records=considered,
            correction_records=[_record(reviewer="doc_high") for _ in range(40)],
        ).assess()

        assert report.reviewer_stats["doc_clean"].considered == 50
        assert report.reviewer_stats["doc_clean"].corrected == 0
        assert [item["reviewer_id"] for item in report.reviewer_outlier_alerts] == [
            "doc_high"
        ]
        assert report.reviewer_outlier_alerts[0]["peer_rate"] == pytest.approx(0.0)

    def test_small_sample_reviewer_is_skipped(self, monkeypatch):
        monkeypatch.setattr(settings, "correction_rate_min_samples", 10)
        monkeypatch.setattr(settings, "correction_rate_reviewer_alert_threshold", 0.50)
        monkeypatch.setattr(settings, "correction_rate_peer_baseline_threshold", 0.20)
        considered = _considered(50, reviewer="doc_high") + _considered(
            50, reviewer="doc_peer"
        )
        # doc_tiny 只有 3 条样本、100% 修正率，不应被判定
        considered = considered + _considered(3, reviewer="doc_tiny")
        records = [_record(reviewer="doc_high") for _ in range(40)] + [
            _record(reviewer="doc_tiny") for _ in range(3)
        ]

        report = CorrectionRateMonitor(
            considered_records=considered, correction_records=records
        ).assess()

        assert [item["reviewer_id"] for item in report.reviewer_outlier_alerts] == [
            "doc_high"
        ]


class TestReportSerialization:
    def test_as_dict_shape_for_dashboard(self, monkeypatch):
        monkeypatch.setattr(settings, "correction_rate_min_samples", 10)
        report = CorrectionRateMonitor(
            considered_records=_considered(100),
            correction_records=[_record(original="TR4", corrected="TR3")],
        ).assess()

        payload = report.as_dict()

        assert set(payload) == {
            "considered",
            "corrected",
            "correction_rate",
            "direction_counts",
            "direction_rates",
            "reviewers",
            "directional_bias_alert",
            "reviewer_outlier_alerts",
            "insufficient_samples",
        }
        assert payload["reviewers"][0]["reviewer_id"] == "doc_1"

    def test_empty_input_is_safe(self):
        report = CorrectionRateMonitor().assess()

        assert report.considered == 0
        assert report.correction_rate == 0.0
        assert report.as_dict()["corrected"] == 0


class TestCollection:
    async def test_single_query_and_adapter(self, monkeypatch):
        """一次查询取回全集，不做 N+1。"""
        rows = [
            SimpleNamespace(
                feedback_type="correction",
                reviewer_id="doc_1",
                content={
                    "field": "diagnosis",
                    "original_value": "TR4",
                    "corrected_value": "TR3",
                },
                created_at=datetime.now(timezone.utc),
            ),
            SimpleNamespace(
                feedback_type="supplement",
                reviewer_id="doc_1",
                content={"field": "x", "original_value": "a", "corrected_value": "b"},
                created_at=datetime.now(timezone.utc),
            ),
        ]

        class _Scalars:
            def all(self):
                return rows

        class _Result:
            def scalars(self):
                return _Scalars()

        class _Session:
            def __init__(self):
                self.calls = 0

            async def execute(self, statement):
                self.calls += 1
                return _Result()

        from app.services import correction_rate_monitor as module

        session = _Session()
        considered, records = await module.collect_correction_records(session)

        assert session.calls == 1, "逐条查询会产生 N+1"
        assert len(considered) == 2
        assert len(records) == 1, "只有 correction 类型才计入修正记录"
