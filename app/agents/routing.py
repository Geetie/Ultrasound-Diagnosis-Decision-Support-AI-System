"""T-GB-AGT-02 Adaptive RAG 动态路由（标准路径 / 全量路径）。

规格：``01-系统架构设计.md`` §5.3「Adaptive RAG 动态路由规则」。

| 难度判定条件 | 路由目标 |
|---|---|
| 单特征→分级映射（"低回声结节→TI-RADS 评估"） | 标准路径 |
| 多特征组合推理（"低回声+边界不清+微钙化+血流信号"） | **经医生确认后**升级为全量路径 |
| 涉及跨器官关联（"甲亢+心律失常"） | 全量路径 |
| 检索结果置信度低于器官级阈值 | 全量路径 |
| 医生显式请求完整诊断 | 全量路径 |

两条铁律：

1. **升级决策权交医生**——只有"多特征组合推理"需要确认对话框；其余
   复杂情形（跨器官、置信度不足）按规格直接走全量路径，因为这两种情况
   "标准路径的初步结论不足以支撑临床决策"，让医生先看结论再决定反而危险。
2. **可升级不可降级**——医生显式指定 ``full`` 时系统绝不降到标准路径；
   指定 ``standard``/``realtime`` 时也只能由医生确认后升级。

判定所依赖的信号全部复用既有能力，不新增文本分类器：

- **特征维度**：``UDTParser.parse()`` 的 ``attributes[].category`` 去重计数
  （为什么不用 LLM：解析器已是知识库构建期的生产路径，判定与入库口径一致，
  且无额外延迟）；
- **器官**：``UDTParser.detect_organs()``（复数版，本次新增——单值检测
  无法表达"跨器官"）；
- **器官级阈值**：直接复用 ``specialist.factory.DEFAULT_ORGAN_THRESHOLDS``，
  避免与专科智能体的落库阈值出现两份数字。

观测（``05-可观测性与运维.md``）：Adaptive RAG 路由分布，标准路径占比
目标 ≥70%、<50% 告警。``AdaptiveRAGRouter.stats()`` 提供该口径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

import structlog

from app.agents.specialist.factory import DEFAULT_ORGAN_THRESHOLDS
from app.knowledge.udt_parser import UDTParser
from app.models.schemas import ResponseTier

logger = structlog.get_logger("app.agents.routing")

# 多特征组合推理的判定阈值：不同征象类别数 ≥ 2 视为多特征
# （规格示例："低回声"=1 类 → 单特征；"低回声+边界不清+微钙化+血流信号"
#  =3 类 → 多特征）
DEFAULT_FEATURE_CATEGORY_THRESHOLD = 2

# 器官级阈值缺失时的兜底（取现有三器官中的最低值，宁可多升级不遗漏）
DEFAULT_ORGAN_THRESHOLD = 0.85

# 两条路径的检索强度
DEFAULT_STANDARD_TOP_K = 10
DEFAULT_FULL_TOP_K = 20


class QueryComplexity(str, Enum):
    """问题复杂度（对应规格表的判定条件）。"""

    SINGLE_FEATURE = "single_feature"
    MULTI_FEATURE = "multi_feature"
    CROSS_ORGAN = "cross_organ"


class RoutingReason(str, Enum):
    """路由判定的具体依据（可观测与审计用）。"""

    SINGLE_FEATURE_GRADING = "single_feature_grading"
    MULTI_FEATURE_NEEDS_CONFIRMATION = "multi_feature_needs_confirmation"
    CROSS_ORGAN_ASSOCIATION = "cross_organ_association"
    LOW_RETRIEVAL_CONFIDENCE = "low_retrieval_confidence"
    DOCTOR_REQUESTED_FULL = "doctor_requested_full"
    DOCTOR_CONFIRMED_UPGRADE = "doctor_confirmed_upgrade"
    DOCTOR_DECLINED_UPGRADE = "doctor_declined_upgrade"


@dataclass(frozen=True)
class RetrievalProfile:
    """标准/全量路径的检索与校验强度。

    ``as_retrieval_kwargs()`` 的键与 ``RetrievalRequest`` 字段同名，可直接
    ``RetrievalRequest(**profile.as_retrieval_kwargs())``。
    """

    use_query_enhancement: bool
    use_reranking: bool
    use_bm25_fallback: bool
    use_llm_enhancement: bool
    top_k: int
    full_validation: bool

    def as_retrieval_kwargs(self) -> Dict[str, Any]:
        return {
            "use_query_enhancement": self.use_query_enhancement,
            "use_reranking": self.use_reranking,
            "use_bm25_fallback": self.use_bm25_fallback,
            "use_llm_enhancement": self.use_llm_enhancement,
            "top_k": self.top_k,
        }

    def as_dict(self) -> Dict[str, Any]:
        payload = self.as_retrieval_kwargs()
        payload["full_validation"] = self.full_validation
        return payload

    @classmethod
    def standard(cls, top_k: int = DEFAULT_STANDARD_TOP_K) -> "RetrievalProfile":
        """标准路径：单特征→分级映射，链路短，只做轻量检索与快校验。"""
        return cls(
            use_query_enhancement=True,
            use_reranking=False,
            use_bm25_fallback=True,
            use_llm_enhancement=False,
            top_k=top_k,
            full_validation=False,
        )

    @classmethod
    def full(cls, top_k: int = DEFAULT_FULL_TOP_K) -> "RetrievalProfile":
        """全量路径：完整检索（精排 + LLM 增强）与完整校验链路。"""
        return cls(
            use_query_enhancement=True,
            use_reranking=True,
            use_bm25_fallback=True,
            use_llm_enhancement=True,
            top_k=top_k,
            full_validation=True,
        )


@dataclass(frozen=True)
class RoutingDecision:
    """一次路由判定的完整结果。"""

    requested_tier: ResponseTier
    effective_tier: ResponseTier
    target_tier: ResponseTier
    complexity: QueryComplexity
    requires_confirmation: bool
    reasons: Tuple[RoutingReason, ...] = ()
    retrieval_profile: RetrievalProfile = field(
        default_factory=lambda: RetrievalProfile.standard()
    )
    feature_categories: Tuple[str, ...] = ()
    detected_organs: Tuple[str, ...] = ()
    retrieval_confidence: Optional[float] = None
    organ_threshold: Optional[float] = None

    @property
    def is_upgraded(self) -> bool:
        """是否已实际升级（相对医生指定档位）。"""
        return (
            self.effective_tier == ResponseTier.FULL
            and self.requested_tier != ResponseTier.FULL
        )

    @property
    def is_standard_path(self) -> bool:
        """当前是否走在标准（非全量）路径上——观测指标口径。"""
        return self.effective_tier != ResponseTier.FULL

    @property
    def pending_upgrade(self) -> bool:
        """是否存在"待医生确认"的升级。"""
        return self.requires_confirmation and self.effective_tier != self.target_tier

    def as_dict(self) -> Dict[str, Any]:
        return {
            "requested_tier": self.requested_tier.value,
            "effective_tier": self.effective_tier.value,
            "target_tier": self.target_tier.value,
            "complexity": self.complexity.value,
            "requires_confirmation": self.requires_confirmation,
            "reasons": [reason.value for reason in self.reasons],
            "is_upgraded": self.is_upgraded,
            "feature_categories": list(self.feature_categories),
            "detected_organs": list(self.detected_organs),
            "retrieval_confidence": self.retrieval_confidence,
            "organ_threshold": self.organ_threshold,
            "retrieval_profile": self.retrieval_profile.as_dict(),
        }


class AdaptiveRAGRouter:
    """按问题难度选择标准/全量路径（含"医生确认"两步式升级）。

    Args:
        udt_parser: UDT 解析器（复用其特征类别与器官识别）。
        feature_category_threshold: 多特征判定阈值（不同类别数 ≥ 该值）。
        organ_thresholds: 器官级置信度阈值表，默认复用专科智能体的默认表。
        default_organ_threshold: 表内缺该器官时的兜底阈值。
        standard_top_k / full_top_k: 两条路径的检索条数。
    """

    def __init__(
        self,
        *,
        udt_parser: Optional[UDTParser] = None,
        feature_category_threshold: int = DEFAULT_FEATURE_CATEGORY_THRESHOLD,
        organ_thresholds: Optional[Dict[str, float]] = None,
        default_organ_threshold: float = DEFAULT_ORGAN_THRESHOLD,
        standard_top_k: int = DEFAULT_STANDARD_TOP_K,
        full_top_k: int = DEFAULT_FULL_TOP_K,
    ) -> None:
        if feature_category_threshold < 1:
            raise ValueError("feature_category_threshold 至少为 1")
        if standard_top_k <= 0 or full_top_k <= 0:
            raise ValueError("top_k 必须为正数")

        self._parser = udt_parser or UDTParser()
        self._feature_threshold = feature_category_threshold
        self._organ_thresholds = dict(
            organ_thresholds if organ_thresholds is not None else DEFAULT_ORGAN_THRESHOLDS
        )
        self._default_organ_threshold = default_organ_threshold
        self._standard_top_k = standard_top_k
        self._full_top_k = full_top_k

        # 观测计数（对应 05-可观测性与运维 的路由分布指标）
        self._total = 0
        self._standard_path = 0
        self._upgraded = 0
        self._confirmation_requested = 0
        self._confirmation_approved = 0
        self._confirmation_declined = 0

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------
    def assess(
        self,
        *,
        query: str,
        target_organ: Optional[str] = None,
        requested_tier: ResponseTier = ResponseTier.FULL,
        retrieval_confidence: Optional[float] = None,
        extra_text: Optional[str] = None,
    ) -> RoutingDecision:
        """给出路由判定。

        Args:
            query: 用户查询/临床文本。
            target_organ: 目标器官（``Organ.value``，如 ``"thyroid"``）。
            requested_tier: 医生显式指定的档位。
            retrieval_confidence: 首轮检索置信度（拿到后二次判定用；
                ``None`` 表示尚未检索，此时不做置信度判定）。
            extra_text: 参与器官/特征判定的补充文本（如语音转写）。
        """
        text = " ".join(part for part in (query, extra_text) if part)

        feature_categories = self._feature_categories(text, target_organ)
        detected_organs = self._detected_organs(text, target_organ)
        threshold = self._threshold_for(target_organ)

        is_cross_organ = len(detected_organs) >= 2
        is_multi_feature = len(feature_categories) >= self._feature_threshold
        is_low_confidence = (
            retrieval_confidence is not None
            and threshold is not None
            and retrieval_confidence < threshold
        )

        reasons: List[RoutingReason] = []
        if is_cross_organ:
            complexity = QueryComplexity.CROSS_ORGAN
            reasons.append(RoutingReason.CROSS_ORGAN_ASSOCIATION)
        elif is_multi_feature:
            complexity = QueryComplexity.MULTI_FEATURE
            reasons.append(RoutingReason.MULTI_FEATURE_NEEDS_CONFIRMATION)
        else:
            complexity = QueryComplexity.SINGLE_FEATURE
            reasons.append(RoutingReason.SINGLE_FEATURE_GRADING)

        if requested_tier == ResponseTier.FULL:
            # 铁律 2：医生显式全量 → 直接全量，且不再需要确认
            reasons.append(RoutingReason.DOCTOR_REQUESTED_FULL)
            if is_low_confidence:
                reasons.append(RoutingReason.LOW_RETRIEVAL_CONFIDENCE)
            decision = self._build(
                requested_tier=requested_tier,
                effective_tier=ResponseTier.FULL,
                target_tier=ResponseTier.FULL,
                complexity=complexity,
                requires_confirmation=False,
                reasons=reasons,
                feature_categories=feature_categories,
                detected_organs=detected_organs,
                retrieval_confidence=retrieval_confidence,
                organ_threshold=threshold,
            )
        elif is_cross_organ or is_low_confidence:
            # 跨器官 / 置信度不足：规格直接给全量路径，不走确认（标准路径
            # 的初步结论在这两种情形下不足以支撑临床决策）
            if is_low_confidence:
                reasons.append(RoutingReason.LOW_RETRIEVAL_CONFIDENCE)
            decision = self._build(
                requested_tier=requested_tier,
                effective_tier=ResponseTier.FULL,
                target_tier=ResponseTier.FULL,
                complexity=complexity,
                requires_confirmation=False,
                reasons=reasons,
                feature_categories=feature_categories,
                detected_organs=detected_organs,
                retrieval_confidence=retrieval_confidence,
                organ_threshold=threshold,
            )
        elif is_multi_feature:
            # 铁律 1：多特征组合推理需医生确认后才升级；确认前先按医生
            # 指定档位走（前端会同时展示初步结论与确认对话框）
            decision = self._build(
                requested_tier=requested_tier,
                effective_tier=requested_tier,
                target_tier=ResponseTier.FULL,
                complexity=complexity,
                requires_confirmation=True,
                reasons=reasons,
                feature_categories=feature_categories,
                detected_organs=detected_organs,
                retrieval_confidence=retrieval_confidence,
                organ_threshold=threshold,
            )
        else:
            decision = self._build(
                requested_tier=requested_tier,
                effective_tier=requested_tier,
                target_tier=requested_tier,
                complexity=complexity,
                requires_confirmation=False,
                reasons=reasons,
                feature_categories=feature_categories,
                detected_organs=detected_organs,
                retrieval_confidence=retrieval_confidence,
                organ_threshold=threshold,
            )

        self._record(decision)
        logger.info("adaptive_rag_routing_decision", **decision.as_dict())
        return decision

    def apply_confirmation(
        self, decision: RoutingDecision, confirmed: bool
    ) -> RoutingDecision:
        """应用医生对"是否升级"的回答，返回最终决策。

        - 医生确认 → 升级到 ``target_tier``；
        - 医生拒绝 → 保持原档位（**不可降级**：仅在 target 高于当前时才谈
          升级，不存在把医生指定的全量路径降成标准路径的分支）。
        """
        if not decision.pending_upgrade:
            return decision

        if confirmed:
            self._confirmation_approved += 1
            final = self._build(
                requested_tier=decision.requested_tier,
                effective_tier=decision.target_tier,
                target_tier=decision.target_tier,
                complexity=decision.complexity,
                requires_confirmation=False,
                reasons=list(decision.reasons)
                + [RoutingReason.DOCTOR_CONFIRMED_UPGRADE],
                feature_categories=decision.feature_categories,
                detected_organs=decision.detected_organs,
                retrieval_confidence=decision.retrieval_confidence,
                organ_threshold=decision.organ_threshold,
            )
        else:
            self._confirmation_declined += 1
            final = self._build(
                requested_tier=decision.requested_tier,
                effective_tier=decision.effective_tier,
                target_tier=decision.target_tier,
                complexity=decision.complexity,
                requires_confirmation=False,
                reasons=list(decision.reasons)
                + [RoutingReason.DOCTOR_DECLINED_UPGRADE],
                feature_categories=decision.feature_categories,
                detected_organs=decision.detected_organs,
                retrieval_confidence=decision.retrieval_confidence,
                organ_threshold=decision.organ_threshold,
            )

        # 修正计数（而非叠加）：判定阶段已按"确认前的档位"计过一次，
        # 确认结果可能改变归类，这里做增量修正，避免同一请求被统计两次。
        if decision.is_standard_path and not final.is_standard_path:
            self._standard_path = max(0, self._standard_path - 1)
        if final.is_upgraded:
            self._upgraded += 1
        logger.info("adaptive_rag_routing_confirmed", confirmed=confirmed, **final.as_dict())
        return final

    # ------------------------------------------------------------------
    # 观测
    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        """路由分布（``05-可观测性与运维`` 口径：标准路径占比 ≥70%）。"""
        standard_ratio = (self._standard_path / self._total) if self._total else 0.0
        return {
            "total": self._total,
            "standard_path": self._standard_path,
            "upgraded": self._upgraded,
            "standard_path_ratio": round(standard_ratio, 4),
            "confirmation_requested": self._confirmation_requested,
            "confirmation_approved": self._confirmation_approved,
            "confirmation_declined": self._confirmation_declined,
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _build(self, **kwargs: Any) -> RoutingDecision:
        effective_tier = kwargs["effective_tier"]
        profile = (
            RetrievalProfile.full(self._full_top_k)
            if effective_tier == ResponseTier.FULL
            else RetrievalProfile.standard(self._standard_top_k)
        )
        return RoutingDecision(retrieval_profile=profile, **kwargs)

    def _record(self, decision: RoutingDecision) -> None:
        self._total += 1
        if decision.is_standard_path:
            self._standard_path += 1
        if decision.is_upgraded:
            self._upgraded += 1
        if decision.requires_confirmation:
            self._confirmation_requested += 1

    def _feature_categories(
        self, text: str, target_organ: Optional[str]
    ) -> Tuple[str, ...]:
        """提取征象类别（去重、排序，保证判定稳定可复现）。"""
        if not text or not text.strip():
            return ()
        try:
            parsed = self._parser.parse(text, organ=target_organ)
        except Exception as exc:  # noqa: BLE001 - 判定失败不得阻断诊断
            logger.warning(
                "adaptive_rag_feature_parse_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return ()
        return tuple(sorted({attr.category for attr in parsed.attributes if attr.category}))

    def _detected_organs(
        self, text: str, target_organ: Optional[str]
    ) -> Tuple[str, ...]:
        """检测文本中提到的器官（含医生指定的目标器官）。"""
        organs: List[str] = []
        if target_organ:
            organs.append(str(target_organ))
        if text and text.strip():
            try:
                detected = self._parser.detect_organs(text)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "adaptive_rag_organ_detection_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                detected = []
            for organ in detected:
                value = getattr(organ, "value", str(organ))
                organs.append(str(value))
        # 去重保序（target_organ 排在最前）
        seen: List[str] = []
        for organ in organs:
            if organ not in seen:
                seen.append(organ)
        return tuple(seen)

    def _threshold_for(self, target_organ: Optional[str]) -> Optional[float]:
        """器官级阈值（表内缺失时用兜底值）。"""
        if self._default_organ_threshold is None:
            return None
        if not target_organ:
            return self._default_organ_threshold
        return self._organ_thresholds.get(
            str(target_organ), self._default_organ_threshold
        )


__all__ = [
    "DEFAULT_FEATURE_CATEGORY_THRESHOLD",
    "DEFAULT_FULL_TOP_K",
    "DEFAULT_ORGAN_THRESHOLD",
    "DEFAULT_STANDARD_TOP_K",
    "AdaptiveRAGRouter",
    "QueryComplexity",
    "RetrievalProfile",
    "RoutingDecision",
    "RoutingReason",
]
