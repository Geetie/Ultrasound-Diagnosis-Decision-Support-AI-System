"""M11 科研量化指标构建。

## 诚实边界（重要）

M11 规格里的字段分两类，本模块对它们区别对待：

1. **可可靠派生**：
   - ``ai_features.feature_vector_hash``：对"本次抽取到的 UDAF 特征集 + 测量
     摘要 + 模型/知识库版本"取 SHA-256 短哈希。它不解释任何临床含义，但让
     科研侧能判断"两次分析是不是同一组输入"，是可复现性的基础；
   - ``confidence_distribution``：由**恶性概率**与**置信度**两个真实数值经
     文档化的确定性变换得到（见 :func:`build_confidence_distribution`）。
     它是"派生视图"，不是模型输出的分布，因此字段说明必须写清来源。
2. **必须显式出现才取**：``elasticity_score`` 与 ``blood_flow``（PSV/EDV/RI）
   需要弹性成像或频谱多普勒，本系统不产出这些模态的数据。只有当报告文本里
   **明确写了带单位的数值**（如"RI 0.72"、"PSV 45cm/s"）才提取；没有就留空。

绝不为了"字段完整"而填充看似合理但无来源的数值。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Sequence

import structlog

from app.models.schemas import (
    AIExtractedFeatures,
    BloodFlowParameters,
    Measurement,
    ModelConfidenceDistribution,
    ResearchMetrics,
)

logger = structlog.get_logger("app.knowledge.research_metrics")

HASH_LENGTH = 16

# 明确带单位的血流参数（缺少单位时不取，避免把无关数字当成测量值）
_BLOOD_FLOW_PATTERNS = {
    "psv": re.compile(r"PSV\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(cm/s|m/s)", re.IGNORECASE),
    "edv": re.compile(r"EDV\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(cm/s|m/s)", re.IGNORECASE),
    "ri": re.compile(r"(?<![A-Za-z])RI\s*[:：]?\s*(0?\.\d+|1\.0|1)\b", re.IGNORECASE),
}
_ELASTICITY_RE = re.compile(r"弹性(?:评分|指数|值)?\s*[:：]?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_blood_flow(
    text: Optional[str], organ: Optional[str] = None
) -> Optional[BloodFlowParameters]:
    """从文本提取血流参数（需显式带单位）。

    单位统一换算为 cm/s（m/s → ×100）。未提及则返回 ``None``。
    """
    del organ
    if not text:
        return None

    params: Dict[str, Optional[float]] = {}
    unit = "cm/s"
    for key in ("psv", "edv"):
        match = _BLOOD_FLOW_PATTERNS[key].search(text)
        if not match:
            continue
        value = _to_float(match.group(1))
        raw_unit = (match.group(2) or "cm/s").lower()
        if raw_unit == "m/s" and value is not None:
            value = round(value * 100.0, 3)
        params[key] = value

    ri_match = _BLOOD_FLOW_PATTERNS["ri"].search(text)
    if ri_match:
        params["ri"] = _to_float(ri_match.group(1))

    if not any(value is not None for value in params.values()):
        return None
    return BloodFlowParameters(
        psv=params.get("psv"),
        edv=params.get("edv"),
        ri=params.get("ri"),
        unit=unit,
    )


def parse_elasticity_score(text: Optional[str]) -> tuple:
    """从文本提取弹性评分（返回 ``(score, unit)``；未提及返回 ``(None, None)``）。"""
    if not text:
        return None, None
    match = _ELASTICITY_RE.search(text)
    if not match:
        return None, None
    return _to_float(match.group(1)), "score"


def build_feature_vector_hash(
    features: Sequence[str],
    measurement: Optional[Measurement] = None,
    model_version: Optional[str] = None,
    kb_version: Optional[str] = None,
) -> Optional[str]:
    """特征集指纹（排序后哈希，保证同输入同输出）。"""
    parts: List[str] = sorted({item for item in features if item})
    if measurement is not None:
        for key in ("long_mm", "short_mm", "anteroposterior_mm", "aspect_ratio", "volume_mm3"):
            value = getattr(measurement, key, None)
            if value is not None:
                parts.append(f"{key}={value}")
    if not parts:
        return None
    parts.append(f"model={model_version or ''}")
    parts.append(f"kb={kb_version or ''}")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:HASH_LENGTH]


def build_confidence_distribution(
    probability: Optional[float],
    confidence: Optional[float],
) -> Optional[ModelConfidenceDistribution]:
    """由恶性概率与置信度派生"良性/恶性/不确定"三元分布（**派生视图**）。

    变换规则（确定性、可复算）：

    - ``malignant = probability``
    - ``indeterminate = (1 - confidence) * (1 - probability)``（模型越不确信，
      越多概率质量落到"不确定"）
    - ``benign = 1 - malignant - indeterminate``

    三者恒非负且和为 1。**不是**模型输出的真实分布，字段说明中必须写清来源。
    """
    if probability is None and confidence is None:
        return None
    prob = 0.0 if probability is None else max(0.0, min(1.0, probability))
    conf = 1.0 if confidence is None else max(0.0, min(1.0, confidence))

    malignant = prob
    indeterminate = (1.0 - conf) * (1.0 - prob)
    benign = max(0.0, 1.0 - malignant - indeterminate)
    total = malignant + indeterminate + benign
    if total <= 0:
        return None
    return ModelConfidenceDistribution(
        benign=round(benign / total, 4),
        malignant=round(malignant / total, 4),
        indeterminate=round(indeterminate / total, 4),
    )


def build_research_metrics(
    features: Sequence[str],
    *,
    measurement: Optional[Measurement] = None,
    probability: Optional[float] = None,
    confidence: Optional[float] = None,
    text: Optional[str] = None,
    model_version: Optional[str] = None,
    kb_version: Optional[str] = None,
) -> Optional[ResearchMetrics]:
    """组装科研量化指标；没有任何可用内容时返回 ``None``。"""
    try:
        feature_hash = build_feature_vector_hash(
            features, measurement, model_version, kb_version
        )
        distribution = build_confidence_distribution(probability, confidence)
        elasticity_score, elasticity_unit = parse_elasticity_score(text)
        blood_flow = parse_blood_flow(text)

        if feature_hash is None and distribution is None and blood_flow is None:
            return None

        return ResearchMetrics(
            elasticity_score=elasticity_score,
            elasticity_unit=elasticity_unit,
            blood_flow=blood_flow,
            ai_features=(
                AIExtractedFeatures(feature_vector_hash=feature_hash)
                if feature_hash
                else None
            ),
            confidence_distribution=distribution,
        )
    except Exception as exc:  # noqa: BLE001 - 科研指标是附加能力
        logger.warning(
            "research_metrics_build_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


__all__ = [
    "HASH_LENGTH",
    "build_confidence_distribution",
    "build_feature_vector_hash",
    "build_research_metrics",
    "parse_blood_flow",
    "parse_elasticity_score",
]
