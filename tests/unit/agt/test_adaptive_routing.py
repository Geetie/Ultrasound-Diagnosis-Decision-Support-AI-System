"""T-GB-AGT-02 Adaptive RAG 动态路由单测。

规格：01-系统架构设计.md §5.3（判定表 + "可升级不可降级"）；
观测口径：05-可观测性与运维.md（标准路径占比 ≥70%）。
"""
import pytest

from app.agents.routing import (
    AdaptiveRAGRouter,
    QueryComplexity,
    RetrievalProfile,
    RoutingReason,
)
from app.models.schemas import ResponseTier
from app.rag.protocols import RetrievalRequest

pytestmark = pytest.mark.track_agt

# 规格示例
SINGLE_FEATURE_QUERY = "低回声结节"
MULTI_FEATURE_QUERY = "低回声+边界不清+微钙化+血流信号"
CROSS_ORGAN_QUERY = "甲亢+心律失常"


def _router(**kwargs):
    return AdaptiveRAGRouter(**kwargs)


# ---------------- 难度判定 ----------------
class TestComplexityClassification:
    def test_single_feature_grading(self):
        decision = _router().assess(
            query=SINGLE_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
        )

        assert decision.complexity == QueryComplexity.SINGLE_FEATURE
        assert decision.feature_categories == ("echogenicity",)
        assert RoutingReason.SINGLE_FEATURE_GRADING in decision.reasons

    def test_multi_feature_combination(self):
        decision = _router().assess(
            query=MULTI_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
        )

        assert decision.complexity == QueryComplexity.MULTI_FEATURE
        assert len(decision.feature_categories) >= 2
        assert "echogenicity" in decision.feature_categories
        assert "boundary" in decision.feature_categories

    def test_cross_organ_association(self):
        decision = _router().assess(
            query=CROSS_ORGAN_QUERY,
            requested_tier=ResponseTier.STANDARD,
        )

        assert decision.complexity == QueryComplexity.CROSS_ORGAN
        assert set(decision.detected_organs) >= {"thyroid", "heart"}

    def test_target_organ_plus_mentioned_organ_is_cross_organ(self):
        decision = _router().assess(
            query="肝脏弥漫性回声改变",
            target_organ="thyroid",  # 医生点开的是甲状腺，但文本在说肝
            requested_tier=ResponseTier.STANDARD,
        )

        assert decision.complexity == QueryComplexity.CROSS_ORGAN
        assert set(decision.detected_organs) >= {"thyroid", "liver"}

    def test_extra_text_participates(self):
        decision = _router().assess(
            query="甲状腺结节",
            extra_text="同时有心律失常",
            requested_tier=ResponseTier.STANDARD,
        )

        assert decision.complexity == QueryComplexity.CROSS_ORGAN

    def test_empty_query_falls_back_to_single_feature(self):
        decision = _router().assess(query="", requested_tier=ResponseTier.STANDARD)

        assert decision.complexity == QueryComplexity.SINGLE_FEATURE
        assert decision.feature_categories == ()

    def test_custom_feature_threshold(self):
        decision = _router(feature_category_threshold=3).assess(
            query=MULTI_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
        )

        # 3 类征象 < 阈值 3 时不算多特征；此处恰为 3 类，仍判为多特征
        assert len(decision.feature_categories) == 3
        assert decision.complexity == QueryComplexity.MULTI_FEATURE


# ---------------- 规格判定表 5 行 ----------------
class TestSpecRoutingTable:
    def test_row1_single_feature_goes_standard(self):
        decision = _router().assess(
            query=SINGLE_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
        )

        assert decision.effective_tier == ResponseTier.STANDARD
        assert decision.requires_confirmation is False
        assert decision.retrieval_profile.full_validation is False

    def test_row2_multi_feature_requires_doctor_confirmation(self):
        decision = _router().assess(
            query=MULTI_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
        )

        # 确认前保持医生指定档位，但已标记待确认与升级目标
        assert decision.effective_tier == ResponseTier.STANDARD
        assert decision.requires_confirmation is True
        assert decision.target_tier == ResponseTier.FULL
        assert decision.pending_upgrade is True
        assert RoutingReason.MULTI_FEATURE_NEEDS_CONFIRMATION in decision.reasons

    def test_row3_cross_organ_goes_full_without_confirmation(self):
        decision = _router().assess(
            query=CROSS_ORGAN_QUERY,
            requested_tier=ResponseTier.STANDARD,
        )

        assert decision.effective_tier == ResponseTier.FULL
        assert decision.requires_confirmation is False
        assert decision.is_upgraded is True

    def test_row4_low_retrieval_confidence_goes_full(self):
        decision = _router().assess(
            query=SINGLE_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
            retrieval_confidence=0.60,  # < 甲状腺阈值 0.85
        )

        assert decision.effective_tier == ResponseTier.FULL
        assert decision.requires_confirmation is False
        assert RoutingReason.LOW_RETRIEVAL_CONFIDENCE in decision.reasons
        assert decision.organ_threshold == pytest.approx(0.85)

    def test_row4_confidence_above_threshold_keeps_standard(self):
        decision = _router().assess(
            query=SINGLE_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
            retrieval_confidence=0.90,
        )

        assert decision.effective_tier == ResponseTier.STANDARD

    def test_row4_missing_confidence_skips_the_check(self):
        decision = _router().assess(
            query=SINGLE_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
            retrieval_confidence=None,
        )

        assert decision.effective_tier == ResponseTier.STANDARD

    def test_row5_doctor_requested_full(self):
        decision = _router().assess(
            query=SINGLE_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.FULL,
        )

        assert decision.effective_tier == ResponseTier.FULL
        assert decision.requires_confirmation is False
        assert RoutingReason.DOCTOR_REQUESTED_FULL in decision.reasons
        assert decision.retrieval_profile.full_validation is True


# ---------------- 铁律 2：可升级不可降级 ----------------
class TestNoDowngrade:
    @pytest.mark.parametrize(
        "query", [SINGLE_FEATURE_QUERY, MULTI_FEATURE_QUERY, CROSS_ORGAN_QUERY]
    )
    def test_full_request_never_downgraded(self, query):
        decision = _router().assess(
            query=query, target_organ="thyroid", requested_tier=ResponseTier.FULL
        )

        assert decision.effective_tier == ResponseTier.FULL
        assert decision.is_standard_path is False

    def test_full_request_with_multi_feature_needs_no_confirmation(self):
        decision = _router().assess(
            query=MULTI_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.FULL,
        )

        assert decision.requires_confirmation is False
        assert decision.pending_upgrade is False

    def test_realtime_request_not_silently_upgraded(self):
        decision = _router().assess(
            query=MULTI_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.REALTIME,
        )

        # 升级必须经医生确认，且确认前保持实时档
        assert decision.effective_tier == ResponseTier.REALTIME
        assert decision.requires_confirmation is True


# ---------------- 铁律 1：升级决策权交医生 ----------------
class TestDoctorConfirmation:
    def test_confirmed_upgrade_applies_full_profile(self):
        router = _router()
        pending = router.assess(
            query=MULTI_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
        )

        final = router.apply_confirmation(pending, confirmed=True)

        assert final.effective_tier == ResponseTier.FULL
        assert final.requires_confirmation is False
        assert final.pending_upgrade is False
        assert RoutingReason.DOCTOR_CONFIRMED_UPGRADE in final.reasons
        assert final.retrieval_profile.use_reranking is True
        assert final.retrieval_profile.use_llm_enhancement is True
        assert final.retrieval_profile.full_validation is True

    def test_declined_upgrade_keeps_standard_conclusion(self):
        router = _router()
        pending = router.assess(
            query=MULTI_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
        )

        final = router.apply_confirmation(pending, confirmed=False)

        assert final.effective_tier == ResponseTier.STANDARD
        assert final.requires_confirmation is False
        assert RoutingReason.DOCTOR_DECLINED_UPGRADE in final.reasons
        assert final.retrieval_profile.use_reranking is False

    def test_confirmation_on_non_pending_decision_is_noop(self):
        router = _router()
        decision = router.assess(
            query=SINGLE_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
        )

        assert router.apply_confirmation(decision, confirmed=True) is decision


# ---------------- 检索强度档位 ----------------
class TestRetrievalProfile:
    def test_standard_profile_is_lighter(self):
        standard = RetrievalProfile.standard(top_k=10)
        full = RetrievalProfile.full(top_k=20)

        assert standard.use_reranking is False
        assert standard.use_llm_enhancement is False
        assert standard.full_validation is False
        assert standard.top_k == 10

        assert full.use_reranking is True
        assert full.use_llm_enhancement is True
        assert full.full_validation is True
        assert full.top_k == 20
        assert full.top_k > standard.top_k

    def test_kwargs_are_accepted_by_retrieval_request(self):
        profile = RetrievalProfile.full(top_k=20)

        request = RetrievalRequest(query="甲状腺结节", organ="thyroid",
                                   **profile.as_retrieval_kwargs())

        assert request.top_k == 20
        assert request.use_reranking is True
        assert request.use_llm_enhancement is True


# ---------------- 阈值来源 ----------------
class TestOrganThresholds:
    def test_defaults_come_from_specialist_factory(self):
        from app.agents.specialist.factory import DEFAULT_ORGAN_THRESHOLDS

        decision = _router().assess(
            query=SINGLE_FEATURE_QUERY,
            target_organ="liver",
            requested_tier=ResponseTier.STANDARD,
            retrieval_confidence=0.80,
        )

        assert decision.organ_threshold == DEFAULT_ORGAN_THRESHOLDS["liver"]

    def test_custom_threshold_table(self):
        router = _router(organ_thresholds={"thyroid": 0.99})

        decision = router.assess(
            query=SINGLE_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
            retrieval_confidence=0.95,
        )

        assert decision.effective_tier == ResponseTier.FULL

    def test_unknown_organ_uses_fallback(self):
        router = _router(default_organ_threshold=0.5)

        decision = router.assess(
            query=SINGLE_FEATURE_QUERY,
            target_organ="spleen",
            requested_tier=ResponseTier.STANDARD,
            retrieval_confidence=0.4,
        )

        assert decision.organ_threshold == pytest.approx(0.5)
        assert decision.effective_tier == ResponseTier.FULL

    def test_invalid_threshold_configuration_rejected(self):
        with pytest.raises(ValueError):
            _router(feature_category_threshold=0)
        with pytest.raises(ValueError):
            _router(standard_top_k=0)


# ---------------- 观测（≥70% 标准路径口径）----------------
class TestRoutingStats:
    def test_all_standard_ratio_is_one(self):
        router = _router()
        for _ in range(3):
            router.assess(
                query=SINGLE_FEATURE_QUERY,
                target_organ="thyroid",
                requested_tier=ResponseTier.STANDARD,
            )

        stats = router.stats()
        assert stats["total"] == 3
        assert stats["standard_path"] == 3
        assert stats["standard_path_ratio"] == pytest.approx(1.0)

    def test_cross_organ_recorded_as_upgrade(self):
        router = _router()
        router.assess(query=CROSS_ORGAN_QUERY, requested_tier=ResponseTier.STANDARD)

        stats = router.stats()
        assert stats["upgraded"] == 1
        assert stats["standard_path"] == 0
        assert stats["standard_path_ratio"] == pytest.approx(0.0)

    def test_confirmation_counters(self):
        router = _router()
        approved = router.assess(
            query=MULTI_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
        )
        declined = router.assess(
            query=MULTI_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
        )
        router.apply_confirmation(approved, confirmed=True)
        router.apply_confirmation(declined, confirmed=False)

        stats = router.stats()
        assert stats["confirmation_requested"] == 2
        assert stats["confirmation_approved"] == 1
        assert stats["confirmation_declined"] == 1
        # 最终归类：拒绝的留在标准路径，确认的升级（同一请求只统计一次）
        assert stats["standard_path"] == 1
        assert stats["upgraded"] == 1
        assert stats["standard_path_ratio"] == pytest.approx(0.5)

    def test_stats_before_any_decision(self):
        assert _router().stats()["standard_path_ratio"] == 0.0


# ---------------- 韧性 ----------------
class TestResilience:
    def test_parser_failure_does_not_break_routing(self):
        class _BrokenParser:
            def parse(self, text, organ=None):
                raise RuntimeError("parser exploded")

            def detect_organs(self, text):
                raise RuntimeError("parser exploded")

        router = _router(udt_parser=_BrokenParser())

        decision = router.assess(
            query=SINGLE_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
        )

        # 判定失败时退化为"单特征 + 目标器官"，路由仍可用
        assert decision.complexity == QueryComplexity.SINGLE_FEATURE
        assert decision.effective_tier == ResponseTier.STANDARD

    def test_decision_serializable(self):
        decision = _router().assess(
            query=MULTI_FEATURE_QUERY,
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
        )

        payload = decision.as_dict()

        assert payload["complexity"] == "multi_feature"
        assert payload["requires_confirmation"] is True
        assert payload["target_tier"] == "full"
        assert isinstance(payload["reasons"], list)
        assert payload["retrieval_profile"]["top_k"] == 10
