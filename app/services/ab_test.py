"""A/B 实验框架（二期 §19）。

按 ``session_id`` 哈希取模分流，判定用卡方检验（α=0.05）、非劣效性检验与提前终止。

三个刻意的设计选择
------------------
1. **分桶复用灰度路由的哈希**（``rollout_router.bucket_of``）：同一套分桶意味着"灰度
   5% 采样"与"实验分流"不会互相打架，也避免两处哈希算法漂移导致同一会话在两个
   系统里落到不同组。
2. **只对 full Profile 生效**：compact 档位本身是降级路径（检索强度更低），把实验
   结论建立在降级路径上会得出"新模型更差"的假象——分不清是模型差还是档位差。
3. **非劣效性优先于显著性**：临床场景里"新版不更差"往往比"新版显著更好"更重要；
   只报 p 值会让"MDE 内略有下降但省了一半时延"这种可接受结果被判成失败。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

import structlog

from app.services.rollout_router import bucket_of

logger = structlog.get_logger()

DEFAULT_ALPHA = 0.05
DEFAULT_POWER = 0.80
DEFAULT_MDE = 0.03


class Variant(str, Enum):
    CONTROL = "control"
    TREATMENT = "treatment"


class Verdict(str, Enum):
    NOT_ENOUGH_DATA = "not_enough_data"
    NO_DIFFERENCE = "no_difference"
    SUPERIOR = "superior"
    INFERIOR = "inferior"
    NON_INFERIOR_ONLY = "non_inferior_only"


class EarlyStop(str, Enum):
    CONTINUE = "continue"
    STOP_HARM = "stop_harm"
    STOP_FUTILITY = "stop_futility"
    STOP_SUPERIOR = "stop_superior"


@dataclass(frozen=True)
class ExperimentConfig:
    """实验定义。"""

    name: str
    enabled: bool = False
    traffic_percent: int = 10
    alpha: float = DEFAULT_ALPHA
    power: float = DEFAULT_POWER
    mde: float = DEFAULT_MDE
    non_inferiority_margin: float = 0.03
    min_samples_per_variant: int = 200

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "traffic_percent": self.traffic_percent,
            "alpha": self.alpha,
            "power": self.power,
            "mde": self.mde,
            "non_inferiority_margin": self.non_inferiority_margin,
            "min_samples_per_variant": self.min_samples_per_variant,
        }


@dataclass
class VariantStats:
    variant: str
    successes: int = 0
    total: int = 0

    @property
    def rate(self) -> float:
        return self.successes / self.total if self.total else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "variant": self.variant,
            "successes": self.successes,
            "total": self.total,
            "rate": round(self.rate, 4),
        }


def assign_variant(
    session_id: str,
    config: ExperimentConfig,
    *,
    profile: str = "full",
) -> Variant:
    """按 session_id 确定性分流；未启用、非 full Profile 或缺 session_id 时归对照。

    缺 session_id 时归对照（而非随机）：拿不到稳定分桶依据时把一个患者随机塞进
    处理组，会让同一个患者的多次检查落到不同组，实验内部一致性被破坏。
    """
    if not config.enabled:
        return Variant.CONTROL
    if str(profile).lower() != "full":
        return Variant.CONTROL
    if not session_id:
        return Variant.CONTROL
    if config.traffic_percent <= 0:
        return Variant.CONTROL

    bucket = bucket_of(session_id)
    if bucket >= config.traffic_percent:
        return Variant.CONTROL
    # 命中实验流量后对半分组：奇偶分桶比再做一次哈希更易解释与复现
    return Variant.TREATMENT if bucket % 2 else Variant.CONTROL


def required_sample_size(
    baseline_rate: float,
    *,
    mde: float = DEFAULT_MDE,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
) -> int:
    """每组所需样本量（两比例比较的正态近似）。

    近似公式：n = 2 * (z_{1-α/2} + z_{power})^2 * p(1-p) / (p * MDE)^2。
    用途是判断"流量够了没"，不是发表统计——保守取整即可，因此不引入 scipy。
    """
    if baseline_rate <= 0 or baseline_rate >= 1:
        raise ValueError("baseline_rate must be in (0, 1)")
    if mde <= 0:
        raise ValueError("mde must be greater than 0")

    z_alpha = _z_for_confidence(1 - alpha / 2)
    z_power = _z_for_confidence(power)
    delta = baseline_rate * mde
    numerator = 2 * (z_alpha + z_power) ** 2 * baseline_rate * (1 - baseline_rate)
    return int(math.ceil(numerator / (delta**2)))


def _z_for_confidence(confidence: float) -> float:
    """正态分位数的近似（避免为两个常数引入 scipy 依赖）。

    用 Abramowitz-Stegun 26.2.23 有理逼近，绝对误差 < 4.5e-4，对样本量估算足够。
    """
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")
    if confidence == 0.5:
        return 0.0
    if confidence < 0.5:
        return -_z_for_confidence(1 - confidence)

    t = math.sqrt(-2.0 * math.log(1.0 - confidence))
    numerator = 2.515517 + 0.802853 * t + 0.010328 * t * t
    denominator = 1.0 + 1.432788 * t + 0.189269 * t * t + 0.001308 * t * t * t
    return t - numerator / denominator


def chi_square_two_proportions(
    control_successes: int,
    control_total: int,
    treatment_successes: int,
    treatment_total: int,
) -> Dict[str, float]:
    """两比例卡方检验（不依赖 scipy：判定逻辑要在任何环境都能跑）。

    返回 ``statistic`` / ``p_value`` / ``dof``；样本为 0 时返回全 0，由调用方按
    "样本不足"处理（不要返回 1.0 当"无差异"，那会掩盖数据缺失）。
    """
    total = control_total + treatment_total
    if total == 0 or control_total == 0 or treatment_total == 0:
        return {"statistic": 0.0, "p_value": 0.0, "dof": 1}

    successes = control_successes + treatment_successes
    failures = total - successes
    expected = [
        (control_total * successes / total, control_total * failures / total),
        (treatment_total * successes / total, treatment_total * failures / total),
    ]
    observed = [
        (control_successes, control_total - control_successes),
        (treatment_successes, treatment_total - treatment_successes),
    ]

    statistic = 0.0
    for row_index in range(2):
        for column_index in range(2):
            exp = expected[row_index][column_index]
            if exp <= 0:
                continue
            statistic += (observed[row_index][column_index] - exp) ** 2 / exp

    return {"statistic": statistic, "p_value": _chi2_sf(statistic, 1), "dof": 1}


def _chi2_sf(statistic: float, dof: int) -> float:
    """卡方分布右尾概率。dof=1 时用正态近似（p = 2 * (1 - Φ(√x))）。"""
    if statistic <= 0:
        return 1.0
    if dof == 1:
        return 2.0 * (1.0 - _normal_cdf(math.sqrt(statistic)))
    raise ValueError("only dof=1 is supported")


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


@dataclass
class ExperimentEvaluation:
    """一次实验评估的结论。"""

    config: ExperimentConfig
    control: VariantStats
    treatment: VariantStats
    verdict: str = Verdict.NOT_ENOUGH_DATA.value
    early_stop: str = EarlyStop.CONTINUE.value
    p_value: float = 1.0
    statistic: float = 0.0
    absolute_lift: float = 0.0
    required_per_variant: int = 0
    reasons: List[str] = field(default_factory=list)

    @property
    def enough_data(self) -> bool:
        return self.verdict != Verdict.NOT_ENOUGH_DATA.value

    def as_dict(self) -> Dict[str, Any]:
        return {
            "experiment": self.config.name,
            "verdict": self.verdict,
            "early_stop": self.early_stop,
            "p_value": round(self.p_value, 6),
            "statistic": round(self.statistic, 6),
            "absolute_lift": round(self.absolute_lift, 4),
            "required_per_variant": self.required_per_variant,
            "control": self.control.as_dict(),
            "treatment": self.treatment.as_dict(),
            "reasons": list(self.reasons),
            "config": self.config.as_dict(),
        }


def evaluate_experiment(
    config: ExperimentConfig,
    *,
    control_successes: int,
    control_total: int,
    treatment_successes: int,
    treatment_total: int,
) -> ExperimentEvaluation:
    """按显著性 + 非劣效性 + 提前终止给出结论。"""
    control = VariantStats(
        variant=Variant.CONTROL.value,
        successes=control_successes,
        total=control_total,
    )
    treatment = VariantStats(
        variant=Variant.TREATMENT.value,
        successes=treatment_successes,
        total=treatment_total,
    )

    evaluation = ExperimentEvaluation(config=config, control=control, treatment=treatment)
    evaluation.absolute_lift = treatment.rate - control.rate

    if control.total == 0 or treatment.total == 0:
        evaluation.reasons.append("某一组样本为 0，无法比较")
        return evaluation

    evaluation.required_per_variant = _safe_required_sample_size(config, control.rate)

    if (
        control.total < config.min_samples_per_variant
        or treatment.total < config.min_samples_per_variant
    ):
        evaluation.reasons.append(
            f"样本不足：两侧各需 {config.min_samples_per_variant}，"
            f"实际 {control.total}/{treatment.total}"
        )
        return evaluation

    result = chi_square_two_proportions(
        control_successes, control.total, treatment_successes, treatment.total
    )
    evaluation.statistic = result["statistic"]
    evaluation.p_value = result["p_value"]

    significant = evaluation.p_value < config.alpha
    non_inferior = evaluation.absolute_lift >= -config.non_inferiority_margin

    if significant and evaluation.absolute_lift > 0:
        evaluation.verdict = Verdict.SUPERIOR.value
    elif significant and evaluation.absolute_lift < 0:
        evaluation.verdict = Verdict.INFERIOR.value
    elif non_inferior:
        # 未达显著但落在非劣效界内：临床上常常是可接受的（例如换来了时延收益）
        evaluation.verdict = Verdict.NON_INFERIOR_ONLY.value
        evaluation.reasons.append(
            f"未达统计显著，但差值 {evaluation.absolute_lift:+.4f} 在非劣效界 "
            f"{-config.non_inferiority_margin:.4f} 内"
        )
    else:
        evaluation.verdict = Verdict.NO_DIFFERENCE.value
        evaluation.reasons.append("未达显著且超出非劣效界，按无差异处理但需关注")
        evaluation.early_stop = EarlyStop.STOP_FUTILITY.value

    evaluation.early_stop = _early_stop_decision(evaluation)
    if evaluation.early_stop != EarlyStop.CONTINUE.value:
        logger.warning(
            "ab_test_early_stop",
            experiment=config.name,
            verdict=evaluation.verdict,
            early_stop=evaluation.early_stop,
            p_value=round(evaluation.p_value, 6),
            lift=round(evaluation.absolute_lift, 4),
        )
    return evaluation


def _safe_required_sample_size(config: ExperimentConfig, baseline_rate: float) -> int:
    try:
        return required_sample_size(
            baseline_rate, mde=config.mde, alpha=config.alpha, power=config.power
        )
    except ValueError:
        # 基线为 0/1 时公式不适用（没有可提升空间）：返回 0 表示"不适用"，
        # 而不是编一个数字出来
        return 0


def _early_stop_decision(evaluation: ExperimentEvaluation) -> str:
    """提前终止判定：有害立即停，明确更优可停，数据够但无效则停（省流量）。"""
    if evaluation.verdict == Verdict.INFERIOR.value:
        return EarlyStop.STOP_HARM.value
    if evaluation.verdict == Verdict.SUPERIOR.value:
        return EarlyStop.STOP_SUPERIOR.value
    required = evaluation.required_per_variant
    if required and min(evaluation.control.total, evaluation.treatment.total) >= required:
        # 已达到设计样本量且无差异：继续跑也只会更贵
        return EarlyStop.STOP_FUTILITY.value
    return EarlyStop.CONTINUE.value


def evaluate_experiment_from_counts(
    config: ExperimentConfig,
    counts: Dict[str, Sequence[int]],
) -> ExperimentEvaluation:
    """便捷入口：``{"control": (successes, total), "treatment": (successes, total)}``。"""
    control = counts.get(Variant.CONTROL.value, (0, 0))
    treatment = counts.get(Variant.TREATMENT.value, (0, 0))
    return evaluate_experiment(
        config,
        control_successes=int(control[0]),
        control_total=int(control[1]),
        treatment_successes=int(treatment[0]),
        treatment_total=int(treatment[1]),
    )


def get_experiment_config() -> ExperimentConfig:
    """从配置构造实验定义（单一来源，避免实验参数散落在代码里）。"""
    from app.config.settings import settings

    return ExperimentConfig(
        name=settings.ab_experiment_name,
        enabled=settings.ab_experiment_enabled,
        traffic_percent=settings.ab_experiment_traffic_percent,
        alpha=settings.ab_alpha,
        power=settings.ab_power,
        mde=settings.ab_mde,
        non_inferiority_margin=settings.ab_non_inferiority_margin,
        min_samples_per_variant=settings.ab_min_samples_per_variant,
    )
