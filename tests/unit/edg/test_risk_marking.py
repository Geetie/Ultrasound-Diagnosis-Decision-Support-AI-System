"""M2 快速路径高危标记（B+ 级）单测（边缘端本地判定）。"""
import pytest

from edge.protocols import NavLabel
from edge.safety import (
    DEFAULT_HIGH_RISK_LABELS,
    DEFAULT_ORGAN_CONFIDENCE_THRESHOLD,
    RISK_CONFIDENCE_MULTIPLIER,
    RISK_FLAG_HIGH,
    SUGGESTED_ACTION_START_STANDARD_EVALUATION,
    LocalSafetyChecker,
)

pytestmark = pytest.mark.track_edg


def _label(name="malignant_suspected", confidence=0.9) -> NavLabel:
    return NavLabel(label=name, x=0.1, y=0.1, width=0.2, height=0.2, confidence=confidence)


class TestRiskThreshold:
    def test_default_multiplier_is_1_05(self):
        assert RISK_CONFIDENCE_MULTIPLIER == 1.05

    def test_threshold_is_organ_base_times_multiplier(self):
        checker = LocalSafetyChecker()
        assert checker.risk_confidence_threshold("thyroid") == pytest.approx(0.60 * 1.05)

    def test_unknown_organ_uses_default_base(self):
        checker = LocalSafetyChecker()
        assert checker.risk_confidence_threshold("nope") == pytest.approx(
            DEFAULT_ORGAN_CONFIDENCE_THRESHOLD * 1.05
        )
        assert checker.risk_confidence_threshold(None) == pytest.approx(
            DEFAULT_ORGAN_CONFIDENCE_THRESHOLD * 1.05
        )

    def test_organ_case_insensitive(self):
        checker = LocalSafetyChecker()
        assert checker.risk_confidence_threshold("THYROID") == pytest.approx(
            checker.risk_confidence_threshold("thyroid")
        )

    def test_custom_thresholds_and_multiplier(self):
        checker = LocalSafetyChecker(
            organ_confidence_thresholds={"thyroid": 0.5}, risk_multiplier=1.10
        )
        assert checker.risk_confidence_threshold("thyroid") == pytest.approx(0.55)

    def test_multiplier_below_one_rejected(self):
        """收紧系数 < 1 会让 B+ 级比普通导航更宽松，必须拒绝。"""
        with pytest.raises(ValueError, match="risk_multiplier"):
            LocalSafetyChecker(risk_multiplier=0.9)


class TestMarkRisk:
    def test_marks_high_risk_label_above_threshold(self):
        checker = LocalSafetyChecker()
        threshold = checker.risk_confidence_threshold("thyroid")
        marked = checker.mark_risk(_label(confidence=threshold + 1e-6), organ="thyroid")
        assert marked.risk_flag == RISK_FLAG_HIGH
        assert marked.suggested_action == SUGGESTED_ACTION_START_STANDARD_EVALUATION

    def test_does_not_mark_at_exactly_threshold(self):
        """规格要求"置信度 > 阈值"，等于阈值不算命中。"""
        checker = LocalSafetyChecker()
        threshold = checker.risk_confidence_threshold("thyroid")
        unmarked = checker.mark_risk(_label(confidence=threshold), organ="thyroid")
        assert unmarked.risk_flag is None
        assert unmarked.suggested_action is None

    def test_does_not_mark_normal_label(self):
        checker = LocalSafetyChecker()
        label = _label(name="organ_name", confidence=0.99)
        assert checker.mark_risk(label, organ="thyroid") is label

    def test_original_label_not_mutated(self):
        """NavLabel 为 frozen dataclass：打标必须返回新对象。"""
        checker = LocalSafetyChecker()
        original = _label(confidence=0.99)
        marked = checker.mark_risk(original, organ="thyroid")
        assert original.risk_flag is None
        assert marked is not original
        assert marked.label == original.label
        assert marked.x == original.x

    def test_cardiac_uses_lower_base_threshold(self):
        checker = LocalSafetyChecker()
        # cardiac 基础阈值 0.55 → B+ 阈值 0.5775，0.60 应命中
        marked = checker.mark_risk(_label(confidence=0.60), organ="cardiac")
        assert marked.risk_flag == RISK_FLAG_HIGH
        # 同一置信度在 thyroid（阈值 0.63）下不命中
        assert checker.mark_risk(_label(confidence=0.60), organ="thyroid").risk_flag is None

    def test_batch_preserves_order_and_length(self):
        checker = LocalSafetyChecker()
        labels = [
            _label(name="organ_name", confidence=0.9),
            _label(name="malignant_suspected", confidence=0.95),
            _label(name="echogenicity", confidence=0.8),
        ]
        marked = checker.mark_risk_batch(labels, organ="thyroid")
        assert len(marked) == 3
        assert [item.label for item in marked] == [item.label for item in labels]
        assert [item.risk_flag for item in marked] == [None, RISK_FLAG_HIGH, None]

    def test_all_default_high_risk_labels_are_markable(self):
        checker = LocalSafetyChecker()
        for name in DEFAULT_HIGH_RISK_LABELS:
            assert checker.mark_risk(_label(name=name, confidence=0.99), organ="thyroid").risk_flag


class TestCheckLabelHighRiskPath:
    def test_high_risk_above_threshold_passes(self):
        checker = LocalSafetyChecker()
        assert checker.check_label(_label(confidence=0.99), organ="thyroid") == []

    def test_high_risk_below_threshold_rejected(self):
        """低于 B+ 阈值的高危标签必须拒绝转发：既不升格，也不作为普通标注外泄。"""
        checker = LocalSafetyChecker()
        problems = checker.check_label(_label(confidence=0.10), organ="thyroid")
        assert any(p.startswith("risk_confidence_below_threshold:") for p in problems)

    def test_high_risk_label_is_not_in_normal_whitelist(self):
        checker = LocalSafetyChecker()
        assert not checker.high_risk_labels & checker.allowed_labels

    def test_unknown_label_still_rejected(self):
        checker = LocalSafetyChecker()
        problems = checker.check_label(_label(name="grading_conclusion", confidence=0.99))
        assert "label_not_whitelisted:grading_conclusion" in problems

    def test_filter_labels_forwards_marked_high_risk(self):
        checker = LocalSafetyChecker()
        labels = [_label(confidence=0.99), _label(confidence=0.10)]
        accepted, violations, rejected = checker.filter_labels(labels, organ="thyroid")
        assert len(accepted) == 1
        assert len(rejected) == 1
        assert violations
        # 通过本地校验后的标注再打 B+ 标记
        marked = checker.mark_risk_batch(accepted, organ="thyroid")
        assert marked[0].risk_flag == RISK_FLAG_HIGH

    def test_check_passes_organ_through(self):
        checker = LocalSafetyChecker()
        result = checker.check({}, [_label(confidence=0.60)], organ="cardiac")
        assert result.passed is True
