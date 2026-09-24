"""M5 恶性概率与 M9 后方回声规则表（知识库侧）。

## 口径说明

**恶性概率（malignancy_risk）与 AI 置信度是两个不同的东西**：
- 置信度 ``confidence`` = "AI 对这个结论有多确信"；
- 恶性概率 ``malignancy_risk.probability`` = "该病灶为恶性的概率估计"。
两者必须分开展示，否则医生会把"模型很确信这是 TR3"误读为"恶性概率高"。

**为什么不直接把 TI-RADS 分数折算成概率**：ACR TI-RADS 2017 的分数是"处置
建议"的分档依据（TR4 = 建议穿刺），把它线性映射成概率会制造出没有依据的
精确数字。因此这里的做法是：把 TI-RADS 的评分项作为**特征贡献值**加总到
一个基线风险上，并**逐项返回依据**（``basis``），医生可展开核对每一条贡献。

**后方回声（M9）**：衰减 → 恶性倾向（TI-RADS 计 1 分）；声影/增强 → 良性
指向（TI-RADS 计 0 分，作为独立良性提示）；无/混合 → 中性。

## 可维护性

所有贡献值集中在模块常量，便于现场按本院标定结果替换；每条特征都带
``source_reference`` 以满足可追溯性要求。真机上线前这些数值必须由医学专家
复核（当前数值来自公开指南的定性描述，**不是**本院标定值）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import structlog

from app.knowledge.vocabulary import UDAFVocabulary, posterior_acoustic_value
from app.models.schemas import (
    MalignancyRisk,
    MalignancyRiskBasis,
    MalignancyRiskLevel,
    Organ,
)

logger = structlog.get_logger("app.knowledge.risk_rules")

# 概率分级阈值
RISK_LEVEL_LOW_MAX = 0.25
RISK_LEVEL_MEDIUM_MAX = 0.60

# 后方回声特征的性质（malignant | benign | neutral）
BIAS_MALIGNANT = "malignant"
BIAS_BENIGN = "benign"
BIAS_NEUTRAL = "neutral"

POSTERIOR_ACOUSTIC_BIAS: Dict[str, str] = {
    "posterior_attenuation": BIAS_MALIGNANT,
    "posterior_shadowing": BIAS_BENIGN,
    "posterior_enhancement": BIAS_BENIGN,
    "posterior_none": BIAS_NEUTRAL,
    "posterior_mixed": BIAS_NEUTRAL,
}

# 后方回声在 TI-RADS 评分中的计分（M9 规格：衰减 +1 / 声影良性 / 增强良性 /
# 无影响）
POSTERIOR_ACOUSTIC_TI_RADS_POINTS: Dict[str, int] = {
    "posterior_attenuation": 1,
    "posterior_shadowing": 0,
    "posterior_enhancement": 0,
    "posterior_none": 0,
    "posterior_mixed": 0,
}

# 纵横比 > 1（"高于宽"）由 M3 自动测量派生，用这个虚拟特征名参与风险计算
FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE = "aspect_ratio_taller_than_wide"
ASPECT_RATIO_TALLER_THAN_WIDE_THRESHOLD = 1.0

REFERENCE_TIRADS = "ACR TI-RADS 2017（定性描述）"
REFERENCE_LIRADS = "LI-RADS v2018（定性描述）"


@dataclass(frozen=True)
class RiskFeature:
    """单条恶性风险特征及其贡献。"""

    feature: str
    label: str
    contribution: float
    source_reference: str


@dataclass(frozen=True)
class OrganRiskModel:
    """器官级恶性风险模型。"""

    organ_code: str
    grading_system: str
    base_risk: float
    features: Tuple[RiskFeature, ...]
    source_reference: str

    def feature_map(self) -> Dict[str, RiskFeature]:
        return {item.feature: item for item in self.features}


def _feature(feature: str, label: str, contribution: float, reference: str) -> RiskFeature:
    return RiskFeature(
        feature=feature, label=label, contribution=contribution, source_reference=reference
    )


# 甲状腺：依据 ACR TI-RADS 2017 的评分项方向（实性低回声、微钙化、边界不规则、
# 纵横比>1 为恶性指向；边界清晰、椭圆形为良性指向）。
THYROID_RISK_MODEL = OrganRiskModel(
    organ_code=Organ.THYROID.value,
    grading_system="TI-RADS",
    base_risk=0.05,
    features=(
        _feature("micro", "微钙化", 0.30, REFERENCE_TIRADS),
        _feature("irregular", "形态不规则", 0.16, REFERENCE_TIRADS),
        _feature("ill-defined", "边界不清", 0.14, REFERENCE_TIRADS),
        _feature("hypoechoic", "低回声", 0.12, REFERENCE_TIRADS),
        _feature(FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE, "纵横比>1", 0.12, REFERENCE_TIRADS),
        _feature("posterior_attenuation", "后方回声衰减", 0.10, REFERENCE_TIRADS),
        _feature("mixed", "混合/丰富血流", 0.06, REFERENCE_TIRADS),
        _feature("lobulated", "分叶状", 0.06, REFERENCE_TIRADS),
        _feature("clear", "边界清晰", -0.10, REFERENCE_TIRADS),
        _feature("oval", "椭圆形/形态规则", -0.10, REFERENCE_TIRADS),
        _feature("posterior_shadowing", "后方声影", -0.15, REFERENCE_TIRADS),
        _feature("posterior_enhancement", "后方回声增强", -0.12, REFERENCE_TIRADS),
        _feature("anechoic", "无回声（囊性）", -0.20, REFERENCE_TIRADS),
    ),
    source_reference=REFERENCE_TIRADS,
)

# 肝脏：依据 LI-RADS v2018 的主要征象方向（动脉期强化、廓清、包膜强化等为
# 恶性指向；无强化、单纯囊性为良性指向）。
LIVER_RISK_MODEL = OrganRiskModel(
    organ_code=Organ.LIVER.value,
    grading_system="LI-RADS",
    base_risk=0.05,
    features=(
        _feature("irregular", "形态不规则", 0.15, REFERENCE_LIRADS),
        _feature("ill-defined", "边界不清", 0.12, REFERENCE_LIRADS),
        _feature("hypoechoic", "低回声", 0.12, REFERENCE_LIRADS),
        _feature("internal", "内部血流", 0.10, REFERENCE_LIRADS),
        _feature(FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE, "纵横比>1", 0.08, REFERENCE_LIRADS),
        _feature("posterior_attenuation", "后方回声衰减", 0.08, REFERENCE_LIRADS),
        _feature("clear", "边界清晰", -0.08, REFERENCE_LIRADS),
        _feature("anechoic", "无回声（囊性）", -0.20, REFERENCE_LIRADS),
        _feature("posterior_enhancement", "后方回声增强", -0.10, REFERENCE_LIRADS),
    ),
    source_reference=REFERENCE_LIRADS,
)

ORGAN_RISK_MODELS: Dict[str, OrganRiskModel] = {
    THYROID_RISK_MODEL.organ_code: THYROID_RISK_MODEL,
    LIVER_RISK_MODEL.organ_code: LIVER_RISK_MODEL,
}


def get_risk_model(organ_code: Optional[str]) -> Optional[OrganRiskModel]:
    """取器官级风险模型；未建模的器官（如心脏）返回 ``None``。

    心脏超声不产出恶性概率——返回 ``None`` 而不是 0.0，是为了让前端区分
    "不适用"与"概率为零"。
    """
    if not organ_code:
        return None
    return ORGAN_RISK_MODELS.get(organ_code.strip().lower())


def ti_rads_points_for_posterior(standard_term: Optional[str]) -> Optional[int]:
    """后方回声的 TI-RADS 计分（非后方回声术语返回 ``None``）。"""
    if not standard_term:
        return None
    return POSTERIOR_ACOUSTIC_TI_RADS_POINTS.get(standard_term)


def posterior_acoustic_bias(standard_term: Optional[str]) -> Optional[str]:
    """后方回声性质的倾向（malignant / benign / neutral）。"""
    if not standard_term:
        return None
    return POSTERIOR_ACOUSTIC_BIAS.get(standard_term)


# ----------------------------------------------------------------------
# UDAF 特征抽取（共享解析器：UDTParser 构造期要建全量 Trie，不能逐次新建）
# ----------------------------------------------------------------------
_shared_parser = None


def set_shared_udt_parser(parser) -> None:
    """替换共享解析器（测试注入用）。"""
    global _shared_parser
    _shared_parser = parser


def get_shared_udt_parser():
    """惰性创建进程级共享 ``UDTParser``。"""
    global _shared_parser
    if _shared_parser is None:
        from app.knowledge.udt_parser import UDTParser

        _shared_parser = UDTParser(vocabulary=UDAFVocabulary())
    return _shared_parser


def extract_udaf_features(text: Optional[str], organ: Optional[str] = None) -> List[str]:
    """从文本抽取 UDAF 标准术语（去重保序）。

    失败返回空列表——特征抽取是增强能力，不能影响诊断主流程。
    """
    if not text or not text.strip():
        return []
    try:
        parsed = get_shared_udt_parser().parse(text, organ=organ)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "udaf_feature_extraction_failed",
            organ=organ,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return []

    seen: List[str] = []
    for attribute in parsed.attributes:
        value = getattr(attribute, "value", None)
        if value and value not in seen:
            seen.append(value)
    return seen


def pick_posterior_acoustic_term(features: Sequence[str]) -> Optional[str]:
    """从 UDAF 术语列表中挑出后方回声**标准术语**（如 ``posterior_attenuation``）。

    若同时命中多个（罕见，通常是表述矛盾），取**恶性倾向**的那个——风险
    判断上宁可偏保守。
    """
    matched = [item for item in features if posterior_acoustic_value(item) is not None]
    if not matched:
        return None
    for term in matched:
        if posterior_acoustic_bias(term) == BIAS_MALIGNANT:
            return term
    return matched[0]


def pick_posterior_acoustic(features: Sequence[str]) -> Optional[str]:
    """从 UDAF 术语列表中挑出后方回声特征（返回契约枚举值）。"""
    term = pick_posterior_acoustic_term(features)
    return posterior_acoustic_value(term)


def _level_of(probability: float) -> MalignancyRiskLevel:
    if probability < RISK_LEVEL_LOW_MAX:
        return MalignancyRiskLevel.LOW
    if probability < RISK_LEVEL_MEDIUM_MAX:
        return MalignancyRiskLevel.MEDIUM
    return MalignancyRiskLevel.HIGH


def compute_malignancy_risk(
    organ_code: Optional[str],
    features: Sequence[str],
) -> Optional[MalignancyRisk]:
    """按器官模型把 UDAF 特征折算为恶性概率。

    Args:
        organ_code: 器官标识（``thyroid`` / ``liver`` / ...）。
        features: UDAF 标准术语列表（可含
            :data:`FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE`）。

    Returns:
        :class:`MalignancyRisk`；器官未建模或未命中任何特征时返回 ``None``
        （"不适用/依据不足"不应伪装成"概率为零"）。
    """
    model = get_risk_model(organ_code)
    if model is None:
        return None

    feature_map = model.feature_map()
    basis: List[MalignancyRiskBasis] = []
    probability = model.base_risk
    seen: set = set()

    for raw in features:
        name = (raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        item = feature_map.get(name)
        if item is None:
            continue
        probability += item.contribution
        basis.append(MalignancyRiskBasis(feature=item.feature, contribution=item.contribution))

    if not basis:
        return None

    probability = round(max(0.0, min(1.0, probability)), 4)
    basis.sort(key=lambda entry: abs(entry.contribution), reverse=True)

    return MalignancyRisk(
        probability=probability,
        level=_level_of(probability),
        basis=basis,
        source_reference=model.source_reference,
    )


__all__ = [
    "ASPECT_RATIO_TALLER_THAN_WIDE_THRESHOLD",
    "BIAS_BENIGN",
    "BIAS_MALIGNANT",
    "BIAS_NEUTRAL",
    "FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE",
    "LIVER_RISK_MODEL",
    "ORGAN_RISK_MODELS",
    "POSTERIOR_ACOUSTIC_BIAS",
    "POSTERIOR_ACOUSTIC_TI_RADS_POINTS",
    "RISK_LEVEL_LOW_MAX",
    "RISK_LEVEL_MEDIUM_MAX",
    "THYROID_RISK_MODEL",
    "OrganRiskModel",
    "RiskFeature",
    "compute_malignancy_risk",
    "extract_udaf_features",
    "get_risk_model",
    "get_shared_udt_parser",
    "pick_posterior_acoustic",
    "pick_posterior_acoustic_term",
    "posterior_acoustic_bias",
    "set_shared_udt_parser",
    "ti_rads_points_for_posterior",
]
