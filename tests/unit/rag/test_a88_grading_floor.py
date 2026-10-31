"""A.88 T4-④ 守卫：分级下限**规则真的存在、默认不生效、留痕完整**。

## 修的真问题（架构真相 §3.3）

「分级下限」登记为**无实现**。临床上它的含义很具体：某些器官的分级体系存在
"报告不得低于某级"的约定（例如已穿刺证实的病例报告仍写 TR2 属明显偏乐观）。

## 三层判据

1. **能力真实存在**：显式规则表 + ``apply_grading_floor`` 能真的把分级抬上去
   （在测试里临时注入规则，证明机制不是空的）；
2. **默认不生效**（关键）：``GRADING_FLOOR_ENABLED=False`` 且规则表为空 ⇒
   **临床输出零变化**；关闭时留痕原因是 ``disabled``（不是"啥也没干"）；
3. **被一条生产路径调用**：``RAGServiceImpl.retrieve`` 每次都评估并留痕
   ``grading_floor_evaluated``。

反证腿：
* 把 ``GRADING_FLOOR_ENABLED`` 改成 True 并把测试里的规则表设空 →
  ``TestDefaultOff`` 红（默认生效会改临床输出）；
* 把 service 里的评估调用删掉 → ``TestWiredIntoService`` 红；
* 把"只抬不降"的判断删掉（改成无条件用 min_grade）→ ``TestOnlyRaises`` 红。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.track_rag]

from app.knowledge import grading_floor as floor_module  # noqa: E402
from app.knowledge.grading_floor import (  # noqa: E402
    GradingFloorRule,
    apply_grading_floor,
    rule_for_organ,
)
from app.knowledge.protocols import KnowledgeChunk  # noqa: E402
from app.rag import service as service_module  # noqa: E402
from app.rag.protocols import (  # noqa: E402
    RetrievalMethod,
    RetrievalRequest,
    RetrievalResult,
    ScoredChunk,
)

_THYROID_RULE = GradingFloorRule(
    organ="thyroid",
    min_grade="4",
    reason="测试规则：已穿刺证实病例报告不得低于 TR4",
    reference="测试用（非临床规则）",
    # 04-3 A1：规则必须带 case 级触发条件（无条件的器官级下限=全器官抬级）
    condition="pathology_proven",
)


@pytest.fixture
def with_rules(monkeypatch):
    """临时注入一条规则（不写进生产规则表 —— 生产表必须留空待专家复核）。"""
    monkeypatch.setattr(floor_module, "GRADING_FLOOR_RULES", (_THYROID_RULE,))
    return _THYROID_RULE


class TestDefaultOff:
    def test_switch_defaults_to_false(self):
        """默认关闭是**已记录的决策**，不是待办（04-3：开关在 settings）。

        读**字段默认值**而非实例值 —— 实例会被部署 .env 覆盖（A.99 已在
        生产 .env 开启该开关做审计留痕），默认语义只有字段定义说了算。
        """
        from app.config.settings import Settings

        assert Settings.model_fields["grading_floor_enabled"].default is False

    def test_production_rule_table_is_empty(self):
        """生产规则表为空 ⇒ 即便有人打开开关，也没有规则能改临床输出。

        要加规则必须先经医学专家复核并在 16 号附录 A 登记影响面（模块文档）。
        """
        assert floor_module.GRADING_FLOOR_RULES == ()

    def test_disabled_returns_grading_unchanged_and_says_why(self, with_rules):
        out = apply_grading_floor(organ="thyroid", grading="2", enabled=False)

        assert out.applied is False
        assert out.grading == "2"
        assert out.reason == "disabled"

    def test_no_rule_is_its_own_reason(self):
        out = apply_grading_floor(organ="heart", grading="mild", enabled=True)

        assert out.applied is False
        assert out.reason == "no_rule"
        assert rule_for_organ("heart") is None


class TestOnlyRaises:
    def test_low_grade_is_raised_to_floor(self, with_rules):
        out = apply_grading_floor(
            organ="thyroid", grading="2", enabled=True,
            conditions=frozenset({"pathology_proven"}),
        )

        assert out.applied is True
        assert out.raised is True
        assert out.grading == "4"
        assert out.original_grading == "2"
        assert out.rule is _THYROID_RULE

    def test_at_floor_is_unchanged(self, with_rules):
        out = apply_grading_floor(
            organ="thyroid", grading="4", enabled=True,
            conditions=frozenset({"pathology_proven"}),
        )

        assert out.applied is False
        assert out.grading == "4"
        assert out.reason == "already_at_or_above"

    def test_above_floor_is_never_lowered(self, with_rules):
        """**反证腿核心**：TR5 不得被"下限 4"降成 4。

        反证：把判断改成"无条件用 rule.min_grade" → 本用例红。
        """
        out = apply_grading_floor(
            organ="thyroid", grading="5", enabled=True,
            conditions=frozenset({"pathology_proven"}),
        )

        assert out.applied is False
        assert out.grading == "5"
        assert out.reason == "already_at_or_above"

    def test_unparseable_grading_is_not_guessed(self, with_rules):
        """解析不出等级 → 如实上报 ``unparseable``，**不许猜一个等级**。"""
        out = apply_grading_floor(
            organ="thyroid", grading="???", enabled=True,
            conditions=frozenset({"pathology_proven"}),
        )

        assert out.applied is False
        assert out.grading == "???"
        assert out.reason == "unparseable"

    def test_missing_grading_is_its_own_reason(self, with_rules):
        out = apply_grading_floor(
            organ="thyroid", grading=None, enabled=True,
            conditions=frozenset({"pathology_proven"}),
        )

        assert out.applied is False
        assert out.reason == "no_grading"

    def test_single_source_of_grading_order(self, with_rules):
        """分级序必须复用 ``grading_definitions``（不另立一套映射）。"""
        from app.inference.grading_definitions import grade_major_level

        # 4a/4b/4c 的主级都是 4：下限 4 时它们都不该被抬
        for sub in ("4a", "4b", "4c"):
            assert grade_major_level("thyroid", sub) == 4
            out = apply_grading_floor(
                organ="thyroid", grading=sub, enabled=True,
                conditions=frozenset({"pathology_proven"}),
            )
            assert out.applied is False, f"{sub} 被误判为低于下限"


class TestWiredIntoService:
    def _mk_service(self, chunks=None):
        enhancer = MagicMock()

        async def _enhance(query, organ_hint=None, use_llm=False):
            out = MagicMock()
            out.enhanced = query
            out.llm_enhanced = False
            return out

        enhancer.enhance = _enhance
        retriever = MagicMock()
        retriever.retrieve = AsyncMock(
            return_value=RetrievalResult(chunks=chunks or [])
        )
        retriever.close = AsyncMock()
        retriever.health_check = AsyncMock(return_value={})
        reranker = MagicMock()
        reranker.close = AsyncMock()
        heuristic = MagicMock()
        heuristic.close = AsyncMock()
        bm25 = MagicMock()
        bm25.should_trigger = MagicMock(return_value=False)
        bm25._index = MagicMock()
        bm25._index.close = AsyncMock()
        return service_module.RAGServiceImpl(
            query_enhancer=enhancer,
            retriever=retriever,
            reranker=reranker,
            heuristic_reranker=heuristic,
            bm25_fallback=bm25,
            rerank_output_top_k=5,
        )

    async def test_service_evaluates_floor_every_request(self, monkeypatch):
        """**核心**：每次检索都要评估（否则"有实现在库里"仍等于壳）。

        反证：删掉 service 里的 ``apply_grading_floor(...)`` 那段 → 红。
        """
        calls: list[tuple] = []

        def _spy(*, organ, grading, enabled=None):
            calls.append((organ, grading, enabled))
            return floor_module.GradingFloorOutcome(
                applied=False, grading=grading, original_grading=grading
            )

        monkeypatch.setattr(floor_module, "apply_grading_floor", _spy)
        svc = self._mk_service()

        await svc.retrieve(RetrievalRequest(query="q", organ="thyroid", top_k=5))

        assert calls, "分级下限没有被评估"
        assert calls[0][0] == "thyroid"

    async def test_report_grading_flows_from_extra(self, monkeypatch):
        """分级由 ``request.extra["report_grading"]`` 提供（调用侧的契约）。"""
        seen: list[str | None] = []

        def _spy(*, organ, grading, enabled=None):
            seen.append(grading)
            return floor_module.GradingFloorOutcome(
                applied=False, grading=grading, original_grading=grading
            )

        monkeypatch.setattr(floor_module, "apply_grading_floor", _spy)
        svc = self._mk_service()

        await svc.retrieve(
            RetrievalRequest(
                query="q",
                organ="thyroid",
                top_k=5,
                extra={"report_grading": "2"},
            )
        )

        assert seen == ["2"]

    async def test_evaluation_leaves_trace(self, monkeypatch):
        # 场景 = "开关关"（部署 .env 现已默认开做审计留痕，A.99）——
        # 这里显式钉回 False，使断言与部署配置解耦。
        from app.config.settings import settings

        monkeypatch.setattr(settings, "grading_floor_enabled", False)
        events: list[tuple[str, dict]] = []

        class _Logger:
            def info(self, event, **fields):
                events.append((event, fields))

            def warning(self, event, **fields):
                events.append((event, fields))

            def error(self, event, **fields):
                events.append((event, fields))

            def debug(self, event, **fields):
                pass

        monkeypatch.setattr(service_module, "logger", _Logger())
        svc = self._mk_service()
        await svc.retrieve(RetrievalRequest(query="q", organ="thyroid", top_k=5))

        payloads = [f for e, f in events if e == "grading_floor_evaluated"]
        assert payloads, "分级下限评估没有留痕"
        assert payloads[0]["reason"] == "disabled"

    async def test_floor_failure_does_not_break_retrieval(self, monkeypatch):
        """规则评估翻车 → 检索照常返回（只多一条 warning）。"""

        def _boom(*args, **kwargs):
            raise RuntimeError("floor exploded")

        monkeypatch.setattr(floor_module, "apply_grading_floor", _boom)
        chunk = KnowledgeChunk(
            chunk_id="c1",
            kb_version="kb_v1.0.0",
            organ="thyroid",
            content="x",
            metadata={},
            embedding=None,
            source_doc_id="d",
            section_path="",
            token_count=4,
        )
        svc = self._mk_service(
            [ScoredChunk(chunk=chunk, score=0.9, retrieval_source=RetrievalMethod.DENSE)]
        )
        result = await svc.retrieve(
            RetrievalRequest(query="q", organ="thyroid", top_k=5, use_reranking=False)
        )
        assert result.has_results is True
