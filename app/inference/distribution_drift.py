"""分布漂移监控（二期 §20）。

既有 ``DriftDetector`` 是 SPC（控制图）路线：对**标量**指标（准确率、幻觉率、时延）
做 2σ/3σ 判异。二期要求的是**分布**漂移：

- 输入分布 KL 散度 > 0.1；
- 输出分布：置信度均值偏移 > 0.05 或 KL > 0.15。

为什么单列一个模块而不是塞进 SPC 检测器
----------------------------------------
两者判据形状不同：SPC 判"某个点超出控制限"，分布漂移判"整条分布形状变了"。
把直方图 KL 硬塞进 ``MetricType`` 标量体系，会让基线的语义含混（一个 float 到底
是准确率还是散度？），也会让既有的 2σ/3σ 逻辑需要分支猜测。分开后各自职责清楚。

两个必须处理的统计学陷阱
------------------------
1. **零概率**：参考分布里某箱为 0、当前分布里该箱有值 -> KL 为 +∞，任何阈值都
   失去意义。这里恒开启拉普拉斯平滑（加最小正数再归一），代价是 KL 略被压缩，
   但换来"不会因为一个罕见箱就报无穷大漂移"。
2. **样本太少**：直方图本身就是噪声，几百个样本估 20 箱必然报漂移。低于
   ``min_samples`` 直接判"样本不足、不判定"，而不是给个数字让人去误读。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()

DEFAULT_BINS = 20
DEFAULT_MIN_SAMPLES = 500
# 平滑常量：比"最小可观测频率"再小一个量级，避免它主导散度
DEFAULT_SMOOTHING = 1e-6


class DistributionType(str, Enum):
    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True)
class DistributionReference:
    """参考分布（基线）。"""

    distribution_type: str
    probabilities: list[float]
    bins: int
    lower: float
    upper: float
    mean: float
    sample_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "distribution_type": self.distribution_type,
            "bins": self.bins,
            "lower": self.lower,
            "upper": self.upper,
            "mean": round(self.mean, 6),
            "sample_count": self.sample_count,
        }


@dataclass
class DistributionDriftResult:
    distribution_type: str
    drifted: bool = False
    insufficient_samples: bool = False
    kl_divergence: float = 0.0
    mean_shift: float = 0.0
    sample_count: int = 0
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "distribution_type": self.distribution_type,
            "drifted": self.drifted,
            "insufficient_samples": self.insufficient_samples,
            "kl_divergence": round(self.kl_divergence, 6),
            "mean_shift": round(self.mean_shift, 6),
            "sample_count": self.sample_count,
            "reasons": list(self.reasons),
        }


def histogram(
    samples: Sequence[float],
    *,
    bins: int = DEFAULT_BINS,
    lower: float,
    upper: float,
    smoothing: float = DEFAULT_SMOOTHING,
) -> list[float]:
    """把样本装进固定区间的直方图并归一化为概率（含拉普拉斯平滑）。

    区间由参考分布固定（``lower``/``upper``），当前批次的越界样本会被夹到边界箱：
    若允许当前批次自定义区间，两次直方图的箱含义就不可比，散度算出来没有意义。
    """
    if bins < 2:
        raise ValueError("bins must be at least 2")
    if upper <= lower:
        raise ValueError("upper must be greater than lower")

    counts = [0.0] * bins
    width = (upper - lower) / bins
    for value in samples:
        position = (float(value) - lower) / width
        index = int(math.floor(position))
        if index < 0:
            index = 0
        elif index >= bins:
            index = bins - 1
        counts[index] += 1.0

    counts = [count + smoothing for count in counts]
    total = sum(counts)
    return [count / total for count in counts]


def kl_divergence(
    reference: Sequence[float], current: Sequence[float], *, smoothing: float = DEFAULT_SMOOTHING
) -> float:
    """KL(reference || current)，单位 nat。

    KL 不对称：这里固定"以参考分布为基准"的写法，避免不同调用点方向不一致导致
    同一现象算出两个数。
    """
    if len(reference) != len(current):
        raise ValueError("reference and current must have the same number of bins")
    if not reference:
        raise ValueError("empty distributions are not comparable")

    total = 0.0
    for ref_probability, current_probability in zip(reference, current):
        ref = max(float(ref_probability), smoothing)
        cur = max(float(current_probability), smoothing)
        total += ref * math.log(ref / cur)
    return max(0.0, total)


def mean_of(samples: Iterable[float]) -> float:
    values = [float(value) for value in samples]
    return sum(values) / len(values) if values else 0.0


class DistributionDriftMonitor:
    """按分布类型维护参考分布并检测漂移。"""

    def __init__(
        self,
        *,
        bins: int = DEFAULT_BINS,
        input_kl_threshold: float | None = None,
        output_kl_threshold: float | None = None,
        output_mean_shift_threshold: float | None = None,
        min_samples: int | None = None,
    ) -> None:
        from app.config.settings import settings

        self._bins = bins
        self._input_kl_threshold = (
            input_kl_threshold
            if input_kl_threshold is not None
            else settings.drift_input_kl_threshold
        )
        self._output_kl_threshold = (
            output_kl_threshold
            if output_kl_threshold is not None
            else settings.drift_output_kl_threshold
        )
        self._output_mean_shift_threshold = (
            output_mean_shift_threshold
            if output_mean_shift_threshold is not None
            else settings.drift_output_mean_shift_threshold
        )
        self._min_samples = (
            min_samples
            if min_samples is not None
            else max(2, settings.drift_distribution_min_samples)
        )
        self._references: dict[str, DistributionReference] = {}

    def set_reference(
        self, distribution_type: str, samples: Sequence[float]
    ) -> DistributionReference:
        """用一批样本建立参考分布（基线）。样本过少时拒绝建立。"""
        values = [float(value) for value in samples]
        if len(values) < self._min_samples:
            raise ValueError(
                f"reference needs at least {self._min_samples} samples, got {len(values)}"
            )

        lower = min(values)
        upper = max(values)
        if upper == lower:
            # 全同值：人为撑开一点区间，否则 upper == lower 无法分箱
            upper = lower + 1e-6

        reference = DistributionReference(
            distribution_type=distribution_type,
            probabilities=histogram(
                values, bins=self._bins, lower=lower, upper=upper
            ),
            bins=self._bins,
            lower=lower,
            upper=upper,
            mean=mean_of(values),
            sample_count=len(values),
        )
        self._references[distribution_type] = reference
        logger.info(
            "distribution_reference_set", **reference.as_dict()
        )
        return reference

    def check(
        self, distribution_type: str, samples: Sequence[float]
    ) -> DistributionDriftResult:
        """检测当前样本相对参考分布是否漂移。"""
        result = DistributionDriftResult(
            distribution_type=distribution_type, sample_count=len(samples)
        )

        reference = self._references.get(distribution_type)
        if reference is None:
            result.reasons.append("尚未建立参考分布，无法判定漂移")
            return result

        if len(samples) < self._min_samples:
            result.insufficient_samples = True
            result.reasons.append(
                f"样本不足（{len(samples)} < {self._min_samples}）：直方图本身是噪声，"
                "此时报漂移没有意义"
            )
            return result

        values = [float(value) for value in samples]
        current = histogram(
            values,
            bins=reference.bins,
            lower=reference.lower,
            upper=reference.upper,
        )
        result.kl_divergence = kl_divergence(reference.probabilities, current)
        result.mean_shift = mean_of(values) - reference.mean

        threshold = (
            self._input_kl_threshold
            if distribution_type == DistributionType.INPUT.value
            else self._output_kl_threshold
        )
        if result.kl_divergence > threshold:
            result.drifted = True
            result.reasons.append(
                f"KL 散度 {result.kl_divergence:.4f} 超过阈值 {threshold}"
            )

        if (
            distribution_type == DistributionType.OUTPUT.value
            and abs(result.mean_shift) > self._output_mean_shift_threshold
        ):
            result.drifted = True
            result.reasons.append(
                f"输出均值偏移 {result.mean_shift:+.4f} 超过阈值 "
                f"{self._output_mean_shift_threshold}"
            )

        if result.drifted:
            logger.warning("distribution_drift_detected", **result.as_dict())
        return result

    def reference(self, distribution_type: str) -> DistributionReference | None:
        return self._references.get(distribution_type)

    def reset(self) -> None:
        self._references.clear()
