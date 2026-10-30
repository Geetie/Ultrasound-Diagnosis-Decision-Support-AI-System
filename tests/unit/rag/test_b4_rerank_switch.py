"""01-2：删摆设开关 rag_use_reranking + 低置信度条件升级。

判据（01-2⑤）：
1. settings 不再含 rag_use_reranking（摆设开关已删，静态断言）；
2. main.py 构造门不再引用它；
3. RetrievalProfile.standard().use_reranking is False / full() True（档位派生不变）；
4. 低置信度且档位非 FULL → retrieval_profile 升 full（use_reranking=True），
   effective_tier 不变（仅检索档升级）；
5. 高置信度 → 仍 standard profile（不是无条件全开）；
6. 灰度退回基线时保留 use_reranking（灰度不再是静默杀手，01-2② #4）；
7. rag/service.py 有 rerank_applied 日志（⑤ 判据 2 的仪表）。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.routing import AdaptiveRAGRouter, RetrievalProfile
from app.config import settings as settings_module
from app.models.schemas import ResponseTier

_REPO_ROOT = Path(__file__).resolve().parents[3]


class TestSwitchRemoved:
    def test_settings_has_no_rag_use_reranking(self):
        assert not hasattr(settings_module.settings, "rag_use_reranking"), (
            "摆设开关应已删除（01-2⑤ 静态判据）"
        )

    def test_main_construction_gate_dropped_it(self):
        src = (_REPO_ROOT / "app" / "main.py").read_text(encoding="utf-8")
        # 注释里允许出现旧名（说明为什么删）；代码不得再引用
        assert "settings.rag_use_reranking" not in src
        assert "or settings.rag_use_reranking" not in src

    def test_profiles_unchanged(self):
        assert RetrievalProfile.standard().use_reranking is False
        assert RetrievalProfile.full().use_reranking is True

    def test_service_has_rerank_applied_log(self):
        src = (_REPO_ROOT / "app" / "rag" / "service.py").read_text(encoding="utf-8")
        assert '"rerank_applied"' in src


class TestLowConfidenceUpgrade:
    """低置信度语义**以生产代码为准**（routing.py :364 既有规则）：
    低置信度/跨器官 → 直接升 FULL 档（_build 对 FULL 给 full 检索档）。

    01-2 实际交付：删摆设开关（条件升级的闸门回到 RetrievalProfile）+
    rerank_applied 日志 + 灰度退回基线时保留精排。"新增仅升检索档的分支"
    经真机环境实测与既有规则重复（死代码），已按"代码为准"回退
    （偏离记录见 16 号附录 A.97）。
    """

    def _router(self) -> AdaptiveRAGRouter:
        return AdaptiveRAGRouter()

    def test_low_confidence_upgrades_to_full_tier_with_full_retrieval(self):
        decision = self._router().assess(
            query="低回声结节 TI-RADS 评估",
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
            retrieval_confidence=0.0,
        )
        assert decision.effective_tier == ResponseTier.FULL
        assert decision.retrieval_profile.use_reranking is True

    def test_realtime_low_confidence_also_upgrades(self):
        """规格规则（:364）不区分请求档：realtime + 低置信 → FULL。"""
        decision = self._router().assess(
            query="低回声结节",
            target_organ="thyroid",
            requested_tier=ResponseTier.REALTIME,
            retrieval_confidence=0.0,
        )
        assert decision.effective_tier == ResponseTier.FULL
        assert decision.retrieval_profile.use_reranking is True

    def test_high_confidence_stays_standard(self):
        decision = self._router().assess(
            query="低回声结节 TI-RADS 评估",
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
            retrieval_confidence=0.99,
        )
        assert decision.effective_tier == ResponseTier.STANDARD
        assert decision.retrieval_profile.use_reranking is False

    def test_no_confidence_unchanged(self):
        """生产现状（retrieval_confidence=None）行为零漂移。"""
        decision = self._router().assess(
            query="低回声结节 TI-RADS 评估",
            target_organ="thyroid",
            requested_tier=ResponseTier.STANDARD,
            retrieval_confidence=None,
        )
        assert decision.retrieval_profile.use_reranking is False


class TestRolloutKeepsReranking:
    @pytest.mark.asyncio
    async def test_rollout_baseline_preserves_use_reranking(self, monkeypatch):
        from app.services import diagnosis_pipeline as dp

        routing = self._decide()
        assert routing.retrieval_profile.use_reranking is True  # full 档

        fake_router = SimpleNamespace(
            decide=AsyncMock(
                return_value=SimpleNamespace(
                    allowed=False, track="full", stage="hold",
                    reason="test", bucket=1, session_index=0,
                )
            )
        )
        monkeypatch.setattr(dp, "get_rollout_router", lambda: fake_router)
        request = SimpleNamespace(session_id="sid_rollout", tier="full")

        routing, rollout = await dp._apply_rollout_gate(request, routing)
        assert rollout.allowed is False
        # 基线退回，但精排保留
        assert routing.retrieval_profile.use_llm_enhancement is False
        assert routing.retrieval_profile.full_validation is False
        assert routing.retrieval_profile.use_reranking is True

    @staticmethod
    def _decide():
        from app.services.diagnosis_pipeline import _assess_routing

        request = SimpleNamespace(
            clinical_text="低回声结节", organ="thyroid", tier="full"
        )
        decision = _assess_routing(request)
        assert decision is not None
        return decision
