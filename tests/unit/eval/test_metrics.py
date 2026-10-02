"""评估指标守卫：钉住**口径**，而不是钉住数字。

## 为什么这样写测试

指标实现的危险不在算错一位小数，而在**口径含糊**：空集合记成好成绩、
"未定义"记成"最差"、重复检出重复计入 TP（那等于"多报几次就能刷高检出率"）、
阈值悄悄缺省。所以本文件的每条用例都对应一条**必须被写死的口径**，
期望值全部由手算得出（合成样本，不涉及任何真实数据，故不构成数值结论）。
"""
from __future__ import annotations

import pytest

from app.evaluation.metrics import (
    Detection,
    box_dice,
    box_iou,
    classification_metrics,
    confusion_matrix,
    detection_rate,
    match_detections,
    roc_auc,
)


class TestConfusionMatrix:
    def test_counts_are_exact(self):
        matrix = confusion_matrix(
            ["A", "A", "B", "B"], ["A", "B", "B", "B"], labels=["A", "B"]
        )

        assert matrix == [[1, 1], [0, 2]]

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="长度必须一致"):
            confusion_matrix(["A"], ["A", "B"], labels=["A", "B"])

    def test_unknown_label_raises_instead_of_being_dropped(self):
        """静默丢弃未知类别会让指标看起来比实际好，必须报错。"""
        with pytest.raises(ValueError, match="labels 之外"):
            confusion_matrix(["A"], ["C"], labels=["A", "B"])


class TestClassificationMetrics:
    def test_known_values(self):
        """y_true=[A,A,B,B], y_pred=[A,B,B,B] → 手算结果。

        A: TP=1, 预测为A的=1, 真实为A的=2 → P=1, R=0.5
        B: TP=2, 预测为B的=3, 真实为B的=2 → P=2/3, R=1
        macroP=(1+2/3)/2=0.8333, macroR=0.75, accuracy=3/4
        """
        m = classification_metrics(
            ["A", "A", "B", "B"], ["A", "B", "B", "B"], labels=["A", "B"]
        )

        assert m.matrix == ((1, 1), (0, 2))
        assert m.per_class_precision["A"] == pytest.approx(1.0)
        assert m.per_class_recall["A"] == pytest.approx(0.5)
        assert m.per_class_precision["B"] == pytest.approx(2 / 3)
        assert m.per_class_recall["B"] == pytest.approx(1.0)
        assert m.macro_precision == pytest.approx((1 + 2 / 3) / 2)
        assert m.macro_recall == pytest.approx(0.75)
        assert m.accuracy == pytest.approx(0.75)
        assert m.micro_precision == pytest.approx(0.75)
        assert m.micro_recall == pytest.approx(0.75)
        assert m.has_valid_accuracy is True

    def test_perfect_predictions(self):
        m = classification_metrics(["A", "B", "B"], ["A", "B", "B"], labels=["A", "B"])

        assert m.macro_f1 == pytest.approx(1.0)
        assert m.accuracy == pytest.approx(1.0)

    def test_empty_input_is_flagged_not_silently_zero_scored(self):
        """空输入：指标为 0.0，但 `has_valid_*` 必须为 False（否则 0 分会被当成结论）。"""
        m = classification_metrics([], [], labels=["A", "B"])

        assert m.accuracy == 0.0
        assert m.has_valid_accuracy is False
        assert m.has_valid_macro is False

    def test_class_never_predicted_is_flagged(self):
        m = classification_metrics(["A", "A"], ["A", "A"], labels=["A", "B"])

        assert m.has_valid_per_class["B"] is False
        assert m.per_class_recall["B"] == 0.0


class TestRocAuc:
    def test_perfect_separation(self):
        assert roc_auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == pytest.approx(1.0)

    def test_inverted_separation(self):
        assert roc_auc([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1]) == pytest.approx(0.0)

    def test_all_tied_scores_are_random_level(self):
        """全并列 → 0.5（秩和法的自然结果，不是近似）。"""
        assert roc_auc([0, 0, 1, 1], [0.5, 0.5, 0.5, 0.5]) == pytest.approx(0.5)
        assert roc_auc([1, 0], [0.5, 0.5]) == pytest.approx(0.5)

    def test_partial_tie_uses_average_ranks(self):
        # 正类分数 [1.0, 0.5]，负类 [0.5, 0.0]：正类秩和 = 4 + 2.5 = 6.5
        # AUC = (6.5 - 2*3/2) / (2*2) = (6.5-3)/4 = 0.875
        assert roc_auc([1, 1, 0, 0], [1.0, 0.5, 0.5, 0.0]) == pytest.approx(0.875)

    def test_single_class_is_undefined_not_zero(self):
        """**本模块最重要的口径**：AUC 无定义时返回 None。

        返回 0.0 等于告诉调用方"完美反向"，是一个会被当成结论的错误。
        """
        assert roc_auc([1, 1, 1], [0.1, 0.2, 0.3]) is None
        assert roc_auc([0, 0], [0.1, 0.2]) is None
        assert roc_auc([], []) is None

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="长度必须一致"):
            roc_auc([1, 0], [0.5])

    def test_non_binary_labels_raise(self):
        with pytest.raises(ValueError, match="只能包含 0/1"):
            roc_auc([1, 2], [0.5, 0.6])


class TestBoxOverlap:
    def test_identical_boxes(self):
        assert box_iou((0, 0, 2, 2), (0, 0, 2, 2)) == pytest.approx(1.0)
        assert box_dice((0, 0, 2, 2), (0, 0, 2, 2)) == pytest.approx(1.0)

    def test_disjoint_boxes(self):
        assert box_iou((0, 0, 1, 1), (5, 5, 1, 1)) == 0.0
        assert box_dice((0, 0, 1, 1), (5, 5, 1, 1)) == 0.0

    def test_known_partial_overlap(self):
        # a=(0,0,2,2) b=(1,0,2,2)：交集 1*2=2，并集 4+4-2=6 → IoU=1/3，Dice=2*2/8=0.5
        assert box_iou((0, 0, 2, 2), (1, 0, 2, 2)) == pytest.approx(1 / 3)
        assert box_dice((0, 0, 2, 2), (1, 0, 2, 2)) == pytest.approx(0.5)

    def test_iou_and_dice_are_not_interchangeable(self):
        """框 IoU 与 Dice 不是线性关系，混用会把阈值口径搞乱。"""
        a, b = (0, 0, 2, 2), (1, 0, 2, 2)
        assert box_iou(a, b) != pytest.approx(box_dice(a, b))

    def test_degenerate_box_returns_zero_not_inf(self):
        assert box_iou((0, 0, 0, 0), (0, 0, 2, 2)) == 0.0
        assert box_dice((0, 0, 0, 0), (0, 0, 0, 0)) == 0.0

    def test_scale_invariant(self):
        """归一化坐标与像素坐标结果相同（IoU/Dice 尺度无关）。"""
        assert box_iou((0, 0, 2, 2), (1, 0, 2, 2)) == pytest.approx(
            box_iou((0, 0, 20, 20), (10, 0, 20, 20))
        )


class TestDetectionMatching:
    GT = ((0, 0, 2, 2), (10, 10, 2, 2))

    def test_perfect_match(self):
        result = match_detections(
            [Detection((0, 0, 2, 2)), Detection((10, 10, 2, 2))],
            self.GT,
            iou_threshold=0.5,
        )

        assert (result.true_positives, result.false_positives, result.false_negatives) == (2, 0, 0)
        assert result.detection_rate == pytest.approx(1.0)
        assert result.precision == pytest.approx(1.0)

    def test_duplicate_detections_do_not_double_count(self):
        """**关键不变量**：同一病灶被重复检出不得重复计入 TP。

        否则"把同一处多报几次"就能把检出率刷上去。
        """
        result = match_detections(
            [
                Detection((0, 0, 2, 2)),
                Detection((0, 0, 2, 2)),
                Detection((0, 0, 2, 2)),
            ],
            self.GT,
            iou_threshold=0.5,
        )

        assert result.true_positives == 1
        assert result.false_positives == 2
        assert result.false_negatives == 1
        assert result.detection_rate == pytest.approx(0.5)

    def test_missing_lesion_reduces_detection_rate(self):
        result = match_detections(
            [Detection((10, 10, 2, 2))], self.GT, iou_threshold=0.5
        )

        assert result.true_positives == 1
        assert result.false_negatives == 1
        assert result.detection_rate == pytest.approx(0.5)

    def test_low_confidence_detections_are_dropped_first(self):
        result = match_detections(
            [Detection((0, 0, 2, 2), confidence=0.5), Detection((10, 10, 2, 2), confidence=0.9)],
            self.GT,
            iou_threshold=0.5,
            confidence_threshold=0.75,
        )

        assert result.total_predictions == 1
        assert result.true_positives == 1
        assert result.false_negatives == 1

    def test_greedy_prefers_higher_iou(self):
        better = Detection((0.1, 0, 2, 2), confidence=0.9)
        worse = Detection((1.0, 0, 2, 2), confidence=0.99)
        result = match_detections([worse, better], [(0, 0, 2, 2)], iou_threshold=0.3)

        assert result.pairs[0][0] == 1, "应优先配对 IoU 更高的那个预测"
        assert result.true_positives == 1

    def test_no_ground_truth_is_flagged(self):
        result = match_detections([Detection((0, 0, 2, 2))], [], iou_threshold=0.5)

        assert result.has_valid_detection_rate is False
        assert result.detection_rate == 0.0

    def test_no_predictions_gives_zero_detection_rate(self):
        result = match_detections([], self.GT, iou_threshold=0.5)

        assert result.true_positives == 0
        assert result.false_negatives == 2
        assert result.detection_rate == 0.0

    def test_iou_threshold_has_no_default(self):
        """阈值必须显式给出：缺省值等于悄悄用了一个没人 review 过的口径。"""
        with pytest.raises(TypeError):
            match_detections([Detection((0, 0, 2, 2))], self.GT)  # type: ignore[call-arg]

    def test_invalid_iou_threshold_raises(self):
        with pytest.raises(ValueError, match="iou_threshold"):
            match_detections([], self.GT, iou_threshold=1.5)

    def test_label_matching_is_opt_in_and_needs_labels(self):
        with pytest.raises(ValueError, match="gt_labels"):
            match_detections(
                [Detection((0, 0, 2, 2), label="nodule")],
                [(0, 0, 2, 2)],
                iou_threshold=0.5,
                require_label_match=True,
            )

    def test_label_mismatch_blocks_match_when_required(self):
        result = match_detections(
            [Detection((0, 0, 2, 2), label="cyst")],
            [(0, 0, 2, 2)],
            iou_threshold=0.5,
            gt_labels=["nodule"],
            require_label_match=True,
        )

        assert result.true_positives == 0

    def test_label_ignored_by_default(self):
        """默认口径：检出率只问"框住没有"，类别正确性由分类指标衡量。"""
        result = match_detections(
            [Detection((0, 0, 2, 2), label="cyst")],
            [(0, 0, 2, 2)],
            iou_threshold=0.5,
            gt_labels=["nodule"],
        )

        assert result.true_positives == 1

    def test_convenience_function_matches_result_property(self):
        args = ([Detection((0, 0, 2, 2))], self.GT)
        assert detection_rate(*args, iou_threshold=0.5) == pytest.approx(
            match_detections(*args, iou_threshold=0.5).detection_rate
        )
