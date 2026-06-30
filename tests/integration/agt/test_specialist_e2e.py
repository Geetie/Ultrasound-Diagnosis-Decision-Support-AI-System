"""AGT 专科智能体 — 端到端集成测试 (T-GA-AGT-04/05/06).

覆盖：
- 三器官 specialist 并行调用（dispatcher.dispatch_all 路径）
- 单器官 P99 延迟（mock LLM，无真实网络） < 5s
- 5 / 7 / 4 等级分级准确率（参数化）
- 端到端：dispatcher → specialist → knowledge → inference

设计原则（与 ``tests/integration/agt/test_real_scenarios.py`` 保持一致）：
- 不依赖真实 LLM / RAG，使用 ``FakeInferenceEngine`` + ``FakeKnowledgeAgent``；
- 模拟延迟通过 ``asyncio.sleep`` 注入，验证 P99 预算；
- 参数化临床病例库覆盖各等级。

运行::

    pytest tests/integration/agt/test_specialist_e2e.py -v
    pytest tests/integration/agt/test_specialist_e2e.py -v -k accuracy
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import List, Optional

import pytest

pytestmark = [
    pytest.mark.track_agt,
    pytest.mark.integration,
]

from app.agents.dispatcher import DispatcherAgentImpl, dispatch_all
from app.agents.protocols import AgentContext, AgentResponse
from app.agents.specialist import (
    CardiacSpecialistAgent,
    LiverSpecialistAgent,
    ThyroidSpecialistAgent,
    build_dispatcher,
)
from app.models.schemas import Organ
from tests.unit.agt.conftest import (
    FakeInferenceEngine,
    FakeKnowledgeAgent,
    make_chunks,
)


# =====================================================================
# 临床病例库（参数化）
# =====================================================================


@dataclass(frozen=True)
class SpecialistCase:
    """单个专科的临床病例。"""

    organ: str
    clinical_text: str
    llm_output: str
    expected_grade: str
    expected_severity_in_content: bool = True


# 甲状腺：5 等级 × 2 = 10 病例
THYROID_CASES = [
    SpecialistCase(
        organ="thyroid",
        clinical_text="甲状腺右叶囊性结节 8mm，边界清晰，TI-RADS 评估",
        llm_output="分级: TR1\n理由: 单纯囊性结节",
        expected_grade="TR1",
    ),
    SpecialistCase(
        organ="thyroid",
        clinical_text="甲状腺结节 5mm，海绵状回声",
        llm_output="分级: TR2\n理由: 海绵状结节",
        expected_grade="TR2",
    ),
    SpecialistCase(
        organ="thyroid",
        clinical_text="甲状腺实性等回声结节 8mm",
        llm_output="分级: TR3\n理由: 实性+等回声",
        expected_grade="TR3",
    ),
    SpecialistCase(
        organ="thyroid",
        clinical_text="甲状腺实性低回声结节 12mm，边缘分叶",
        llm_output="分级: TR4\n总分: 7\n理由: 实性+低回声+分叶+粗大钙化",
        expected_grade="TR4",
    ),
    SpecialistCase(
        organ="thyroid",
        clinical_text="甲状腺实性极低回声结节 15mm，纵横比>1，微钙化",
        llm_output="分级: TR5\n总分: 14\n理由: 极低回声+高>宽+微钙化",
        expected_grade="TR5",
    ),
    # 重复样本（用于统计 P99 / 准确率）
    SpecialistCase(
        organ="thyroid",
        clinical_text="甲状腺实性低回声结节 10mm，边缘不规则",
        llm_output="分级: TR4\n总分: 8",
        expected_grade="TR4",
    ),
]

# 肝脏：核心 5 等级 + LR-M + LR-TIV = 7 病例
LIVER_CASES = [
    SpecialistCase(
        organ="liver",
        clinical_text="肝囊肿 2cm",
        llm_output="分级: LR-1\n理由: 单纯囊肿",
        expected_grade="LR-1",
    ),
    SpecialistCase(
        organ="liver",
        clinical_text="肝血管瘤 1.5cm",
        llm_output="分级: LR-2\n理由: 典型血管瘤",
        expected_grade="LR-2",
    ),
    SpecialistCase(
        organ="liver",
        clinical_text="肝右叶 1.2cm 病灶，APHE 弱，无廓清",
        llm_output="分级: LR-3\n理由: <2cm + 弱 APHE",
        expected_grade="LR-3",
    ),
    SpecialistCase(
        organ="liver",
        clinical_text="肝左叶 1.8cm 病灶，APHE，无廓清无包膜",
        llm_output="分级: LR-4\n理由: APHE 但无廓清包膜",
        expected_grade="LR-4",
    ),
    SpecialistCase(
        organ="liver",
        clinical_text="肝右叶 2.5cm 病灶，APHE+廓清+包膜+阈值增长",
        llm_output="分级: LR-5\n理由: 满足 LR-5 标准",
        expected_grade="LR-5",
    ),
    SpecialistCase(
        organ="liver",
        clinical_text="肝左叶 3cm 病灶，rim APHE",
        llm_output="分级: LR-M\n理由: 边缘强化",
        expected_grade="LR-M",
    ),
    SpecialistCase(
        organ="liver",
        clinical_text="门静脉主干受侵",
        llm_output="分级: LR-TIV\n理由: 明确肿瘤侵犯",
        expected_grade="LR-TIV",
    ),
]

# 心脏：4 档 LVEF 严重程度
CARDIAC_CASES = [
    SpecialistCase(
        organ="cardiac",
        clinical_text="EF 65%",
        llm_output="LVEF: 65%\n分级: normal",
        expected_grade="normal",
    ),
    SpecialistCase(
        organ="cardiac",
        clinical_text="EF 50%",
        llm_output="LVEF: 50%\n分级: mildly_reduced",
        expected_grade="mildly_reduced",
    ),
    SpecialistCase(
        organ="cardiac",
        clinical_text="EF 38%",
        llm_output="LVEF: 38%\n分级: moderately_reduced",
        expected_grade="moderately_reduced",
    ),
    SpecialistCase(
        organ="cardiac",
        clinical_text="EF 22%",
        llm_output="LVEF: 22%\n分级: severely_reduced",
        expected_grade="severely_reduced",
    ),
    # 重复样本
    SpecialistCase(
        organ="cardiac",
        clinical_text="EF 60%",
        llm_output="LVEF: 60%\n分级: normal",
        expected_grade="normal",
    ),
]


# =====================================================================
# Helper：构造 specialist
# =====================================================================


def _make_specialist(
    organ: Organ,
    llm_output: str,
    latency_seconds: float = 0.0,
) -> object:
    """构造注入 mock LLM + RAG 的 specialist。"""
    rag = FakeKnowledgeAgent(chunks=make_chunks(5, organ=organ.value))
    inference = FakeInferenceEngine(
        text=llm_output,
        latency_ms=latency_seconds * 1000.0,
    )
    if organ == Organ.THYROID:
        return ThyroidSpecialistAgent(inference_engine=inference, knowledge_agent=rag)
    elif organ == Organ.LIVER:
        return LiverSpecialistAgent(inference_engine=inference, knowledge_agent=rag)
    elif organ == Organ.CARDIAC:
        return CardiacSpecialistAgent(inference_engine=inference, knowledge_agent=rag)
    raise ValueError(f"unknown organ: {organ}")


def _ctx(organ: Organ, query: str, req_id: str) -> AgentContext:
    return AgentContext(
        request_id=req_id,
        session_id="s1",
        organ=organ,
        user_query=query,
    )


# =====================================================================
# 1) 准确率：各器官
# =====================================================================


class TestAccuracy:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", THYROID_CASES, ids=lambda c: c.clinical_text)
    async def test_thyroid_accuracy(self, case):
        s = _make_specialist(Organ.THYROID, case.llm_output)
        resp = await s.invoke(_ctx(Organ.THYROID, case.clinical_text, "r1"))
        assert case.expected_grade in resp.content
        assert resp.confidence > 0.0
        assert resp.organ == Organ.THYROID

    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", LIVER_CASES, ids=lambda c: c.clinical_text)
    async def test_liver_accuracy(self, case):
        s = _make_specialist(Organ.LIVER, case.llm_output)
        resp = await s.invoke(_ctx(Organ.LIVER, case.clinical_text, "r1"))
        assert case.expected_grade in resp.content
        assert resp.confidence > 0.0
        assert resp.organ == Organ.LIVER

    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", CARDIAC_CASES, ids=lambda c: c.clinical_text)
    async def test_cardiac_accuracy(self, case):
        s = _make_specialist(Organ.CARDIAC, case.llm_output)
        resp = await s.invoke(_ctx(Organ.CARDIAC, case.clinical_text, "r1"))
        assert case.expected_grade in resp.content
        assert resp.confidence > 0.0
        assert resp.organ == Organ.CARDIAC


# =====================================================================
# 2) P99 延迟：单器官连续 20 次调用
# =====================================================================


class TestP99Latency:
    @pytest.mark.asyncio
    async def test_thyroid_p99_under_5s(self):
        """甲状腺 P99 < 5s（mock LLM 无网络延迟）。"""
        # 注入 50ms 模拟推理延迟
        s = _make_specialist(
            Organ.THYROID,
            "分级: TR4\n总分: 5",
            latency_seconds=0.05,
        )
        latencies: List[float] = []
        for i in range(20):
            start = time.monotonic()
            resp = await s.invoke(_ctx(Organ.THYROID, f"query {i}", f"r{i}"))
            latencies.append((time.monotonic() - start) * 1000.0)
            assert resp.confidence > 0.0
        # P99 (近似 top 5%): 排序后取 index=int(0.99 * 20) - 1 = 18
        latencies.sort()
        p99 = latencies[int(0.99 * len(latencies)) - 1]
        assert p99 < 5000.0, f"thyroid P99 latency {p99:.1f}ms exceeds 5s"

    @pytest.mark.asyncio
    async def test_liver_p99_under_5s(self):
        s = _make_specialist(
            Organ.LIVER, "分级: LR-4", latency_seconds=0.05
        )
        latencies: List[float] = []
        for i in range(20):
            start = time.monotonic()
            resp = await s.invoke(_ctx(Organ.LIVER, f"query {i}", f"r{i}"))
            latencies.append((time.monotonic() - start) * 1000.0)
            assert resp.confidence > 0.0
        latencies.sort()
        p99 = latencies[int(0.99 * len(latencies)) - 1]
        assert p99 < 5000.0, f"liver P99 latency {p99:.1f}ms exceeds 5s"

    @pytest.mark.asyncio
    async def test_cardiac_p99_under_5s(self):
        s = _make_specialist(
            Organ.CARDIAC, "EF 50%, mildly_reduced", latency_seconds=0.05
        )
        latencies: List[float] = []
        for i in range(20):
            start = time.monotonic()
            resp = await s.invoke(_ctx(Organ.CARDIAC, f"query {i}", f"r{i}"))
            latencies.append((time.monotonic() - start) * 1000.0)
            assert resp.confidence > 0.0
        latencies.sort()
        p99 = latencies[int(0.99 * len(latencies)) - 1]
        assert p99 < 5000.0, f"cardiac P99 latency {p99:.1f}ms exceeds 5s"


# =====================================================================
# 3) 并行调用：dispatcher.dispatch_all
# =====================================================================


class TestParallelDispatch:
    @pytest.mark.asyncio
    async def test_three_organs_parallel(self):
        """三个器官并行调用：dispatcher.dispatch_all。"""
        rag = FakeKnowledgeAgent(chunks=make_chunks(5, organ="thyroid"))
        inference = FakeInferenceEngine(text="分级: TR4")
        dispatcher = build_dispatcher(
            inference_engine=inference,
            knowledge_agent=rag,
        )
        # 使用 dispatch_all 并行调用 3 个器官
        responses = await dispatch_all(
            dispatcher,
            _ctx(Organ.THYROID, "multi-organ query", "r1"),
            [Organ.THYROID, Organ.LIVER, Organ.CARDIAC],
        )
        assert len(responses) == 3
        assert [r.organ for r in responses] == [
            Organ.THYROID, Organ.LIVER, Organ.CARDIAC,
        ]
        # 由于 LLM 输出对所有器官都是 "TR4"，
        # thyroid → 成功（TR4 命中），
        # liver → grade_unparseable（无 LR-*），
        # cardiac → grade_unparseable（无 LVEF）。
        thyroid_resp = responses[0]
        liver_resp = responses[1]
        cardiac_resp = responses[2]

        assert "TR4" in thyroid_resp.content
        assert thyroid_resp.confidence > 0.0

        assert liver_resp.confidence == 0.0
        assert "grade_unparseable" in liver_resp.content

        assert cardiac_resp.confidence == 0.0
        assert "lvef_unparseable" in cardiac_resp.content

    @pytest.mark.asyncio
    async def test_three_organs_with_correct_outputs(self):
        """三器官 LLM 输出各自正确时，全部成功。"""
        # 构造一个会按 organ 给出不同输出的 inference engine
        class OrganAwareInference:
            def __init__(self):
                self.call_count = 0
                self.last_request = None

            async def generate(self, request):
                self.call_count += 1
                self.last_request = request
                # 根据 prompt 关键词判断器官（注意：「tr」过于宽泛，会
                # 匹配 "ultrasound" 等无关词，所以改用「tr1」「tr2」等
                # 具体分级码前缀 — 仅在甲状腺 prompt 中出现）
                prompt = request.prompt.lower()
                if "ti-rads" in prompt or "tr1" in prompt or "tr2" in prompt or "tr3" in prompt:
                    text = "分级: TR4"
                elif "li-rads" in prompt or "lr-" in prompt:
                    text = "分级: LR-4"
                elif "lvef" in prompt or "ase" in prompt:
                    text = "LVEF: 50%, mildly_reduced"
                else:
                    text = "无法判断"
                from app.inference.protocols import InferenceResponse, TokenUsage
                return InferenceResponse(
                    text=text, usage=TokenUsage(), finish_reason="stop"
                )

            async def health_check(self): return True
            async def get_model_info(self): return None
            async def close(self): return None

        rag = FakeKnowledgeAgent(chunks=make_chunks(5))
        inference = OrganAwareInference()
        dispatcher = build_dispatcher(inference_engine=inference, knowledge_agent=rag)

        responses = await dispatch_all(
            dispatcher,
            _ctx(Organ.THYROID, "multi-organ", "r1"),
            [Organ.THYROID, Organ.LIVER, Organ.CARDIAC],
        )
        # 三个器官均应有 confidence > 0
        for resp in responses:
            assert resp.confidence > 0.0, f"{resp.organ} failed: {resp.content}"
        assert "TR4" in responses[0].content
        assert "LR-4" in responses[1].content
        assert "mildly_reduced" in responses[2].content


# =====================================================================
# 4) 端到端：dispatcher.invoke 单器官
# =====================================================================


class TestDispatcherInvoke:
    @pytest.mark.asyncio
    async def test_dispatcher_routes_to_thyroid(self):
        """dispatcher.invoke 路由到甲状腺 specialist。"""
        rag = FakeKnowledgeAgent(chunks=make_chunks(5, organ="thyroid"))
        inference = FakeInferenceEngine(text="分级: TR3\n理由: 实性等回声")
        dispatcher = build_dispatcher(inference_engine=inference, knowledge_agent=rag)
        resp = await dispatcher.invoke(_ctx(Organ.THYROID, "甲状腺结节", "r1"))
        assert resp.confidence > 0.0
        assert "TR3" in resp.content
        # 聚合后 organ 应保持原值
        assert resp.organ == Organ.THYROID

    @pytest.mark.asyncio
    async def test_dispatcher_routes_to_liver(self):
        rag = FakeKnowledgeAgent(chunks=make_chunks(5, organ="liver"))
        inference = FakeInferenceEngine(text="分级: LR-5")
        dispatcher = build_dispatcher(inference_engine=inference, knowledge_agent=rag)
        resp = await dispatcher.invoke(_ctx(Organ.LIVER, "肝占位", "r1"))
        assert "LR-5" in resp.content
        assert resp.organ == Organ.LIVER

    @pytest.mark.asyncio
    async def test_dispatcher_routes_to_cardiac(self):
        rag = FakeKnowledgeAgent(chunks=make_chunks(5, organ="cardiac"))
        inference = FakeInferenceEngine(text="LVEF: 38%, moderately_reduced")
        dispatcher = build_dispatcher(inference_engine=inference, knowledge_agent=rag)
        resp = await dispatcher.invoke(_ctx(Organ.CARDIAC, "心脏 EF 38%", "r1"))
        assert "moderately_reduced" in resp.content
        assert resp.organ == Organ.CARDIAC


# =====================================================================
# 5) 降级路径：缺依赖
# =====================================================================


class TestDegradationE2E:
    @pytest.mark.asyncio
    async def test_placeholder_dispatcher_when_no_deps(self):
        """无 inference / knowledge 时，dispatcher 用占位 specialist。"""
        dispatcher = build_dispatcher(
            inference_engine=None,
            knowledge_agent=None,
            use_placeholder_on_missing=True,
        )
        resp = await dispatcher.invoke(_ctx(Organ.THYROID, "甲状腺结节", "r1"))
        # 占位返回 confidence=0.0
        assert resp.confidence == 0.0
        assert "specialist_pending" in resp.content

    @pytest.mark.asyncio
    async def test_no_dispatcher_when_missing_and_strict(self):
        """严格模式下，缺依赖时抛 ValueError。"""
        with pytest.raises(ValueError, match="required"):
            build_dispatcher(
                inference_engine=None,
                knowledge_agent=None,
                use_placeholder_on_missing=False,
            )


# =====================================================================
# 6) Health check：端到端
# =====================================================================


class TestHealthCheckE2E:
    @pytest.mark.asyncio
    async def test_all_healthy(self):
        rag = FakeKnowledgeAgent()
        inference = FakeInferenceEngine()
        dispatcher = build_dispatcher(inference, rag)
        assert await dispatcher.health_check() is True

    @pytest.mark.asyncio
    async def test_one_unhealthy_still_healthy(self):
        """RAG 不健康时，specialist 仍视为健康（graceful degradation）。"""
        class UnhealthyRAG:
            async def query(self, *args, **kwargs):
                return []
            async def health_check(self): return False
            async def close(self): return None

        inference = FakeInferenceEngine()
        dispatcher = build_dispatcher(inference, UnhealthyRAG())
        # RAG 不健康但 LLM 健康 → specialist 仍健康
        assert await dispatcher.health_check() is True
