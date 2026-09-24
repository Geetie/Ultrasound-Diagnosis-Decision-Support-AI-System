"""专科智能体 M3/M5/M9 临床属性增强单测（公共流程钩子）。"""
import pytest

from app.agents.protocols import AgentContext, AgentResponse
from app.agents.specialist.thyroid import ThyroidSpecialistAgent
from app.models.schemas import Organ, PosteriorAcoustic
from tests.unit.agt.conftest import (
    FakeInferenceEngine,
    FakeKnowledgeAgent,
    make_chunks,
)

pytestmark = pytest.mark.track_agt


def _ctx(query: str = "甲状腺结节 1.2x0.8cm 后方回声衰减") -> AgentContext:
    return AgentContext(
        request_id="r1", session_id="s1", organ=Organ.THYROID, user_query=query
    )


@pytest.fixture
def rag():
    return FakeKnowledgeAgent(chunks=make_chunks(3, organ="thyroid"))


def _specialist(rag, text: str) -> ThyroidSpecialistAgent:
    return ThyroidSpecialistAgent(
        inference_engine=FakeInferenceEngine(text=text), knowledge_agent=rag
    )


class TestEnrichmentThroughInvoke:
    @pytest.mark.asyncio
    async def test_enriches_malignancy_risk_and_posterior_acoustic(self, rag):
        specialist = _specialist(
            rag,
            "分级: TR4\n理由: 低回声、形态不规则、边界不清、微钙化，后方回声衰减",
        )
        response = await specialist.invoke(_ctx())

        assert response.posterior_acoustic == PosteriorAcoustic.ATTENUATION
        assert response.malignancy_risk is not None
        assert response.malignancy_risk.probability > 0
        assert any(
            item.feature == "micro" for item in response.malignancy_risk.basis
        )

    @pytest.mark.asyncio
    async def test_structured_measurement_extracted(self, rag):
        specialist = _specialist(rag, "分级: TR3\n大小 1.2x0.8cm")
        response = await specialist.invoke(_ctx())

        assert response.measurement is not None
        assert response.measurement.long_mm == 12.0
        assert response.measurement.aspect_ratio == 1.5

    @pytest.mark.asyncio
    async def test_trace_records_measurement_and_posterior_and_risk(self, rag):
        specialist = _specialist(
            rag, "分级: TR4\n低回声，后方回声衰减，大小 1.2x0.8cm"
        )
        response = await specialist.invoke(_ctx())

        trace = "\n".join(response.reasoning_trace)
        assert "measurement_source -> text" in trace
        assert "posterior_acoustic -> posterior_attenuation" in trace
        assert "malignancy_risk ->" in trace

    @pytest.mark.asyncio
    async def test_ti_rads_points_recorded_for_thyroid(self, rag):
        """M9 规格：后方回声衰减在 TI-RADS 中计 1 分，需可追溯。"""
        specialist = _specialist(rag, "分级: TR4\n后方回声衰减")
        response = await specialist.invoke(_ctx())

        assert "posterior_acoustic_ti_rads_points -> 1" in "\n".join(
            response.reasoning_trace
        )

    @pytest.mark.asyncio
    async def test_no_features_leaves_fields_empty(self, rag):
        """无特征时不得写入空概率/空测量（"未产出"与"结果为空"语义不同）。"""
        specialist = _specialist(rag, "分级: TR2\n未见明显异常")
        response = await specialist.invoke(_ctx())

        assert response.malignancy_risk is None
        assert response.measurement is None
        assert response.posterior_acoustic is None

    @pytest.mark.asyncio
    async def test_aspect_ratio_feeds_risk(self, rag):
        with_ratio = await _specialist(rag, "分级: TR4\n低回声，大小 1.5x0.8cm").invoke(_ctx())
        without_ratio = await _specialist(rag, "分级: TR4\n低回声").invoke(_ctx())

        assert with_ratio.measurement.aspect_ratio > 1.0
        assert with_ratio.malignancy_risk.probability > without_ratio.malignancy_risk.probability

    @pytest.mark.asyncio
    async def test_degraded_response_is_not_enriched(self, rag):
        """RAG 为空时走降级分支，不应凭空产生临床属性。"""
        specialist = ThyroidSpecialistAgent(
            inference_engine=FakeInferenceEngine(text="分级: TR4"),
            knowledge_agent=FakeKnowledgeAgent(chunks=[]),
        )
        response = await specialist.invoke(_ctx())

        assert response.confidence == 0.0
        assert response.malignancy_risk is None
        assert response.posterior_acoustic is None


class TestDifferentialDiagnosesThroughInvoke:
    @pytest.mark.asyncio
    async def test_parses_differential_list(self, rag):
        specialist = _specialist(
            rag,
            "分级: TR4\n鉴别诊断:\n"
            "1. 乳头状癌 | 0.60 | 支持: 微钙化 | 排除: 边界清晰\n"
            "2. 结节性甲状腺肿 | 0.30 | 支持: 混合回声\n",
        )
        response = await specialist.invoke(_ctx())

        assert [item.rank for item in response.differential_diagnoses] == [1, 2]
        assert response.differential_diagnoses[0].disease_name == "乳头状癌"
        assert response.differential_diagnoses[0].confidence == 0.6
        assert "differential_diagnoses ->" in "\n".join(response.reasoning_trace)

    @pytest.mark.asyncio
    async def test_no_differential_block_leaves_empty(self, rag):
        specialist = _specialist(rag, "分级: TR2\n未见明显异常")
        response = await specialist.invoke(_ctx())

        assert response.differential_diagnoses == []

    def test_falls_back_to_chunk_text(self):
        from tests.unit.agt.conftest import make_chunk

        chunk = make_chunk(
            chunk_id="k1", content="鉴别诊断:\n1. 乳头状癌 | 0.5 | 支持: 微钙化\n"
        )
        specialist = ThyroidSpecialistAgent(
            inference_engine=FakeInferenceEngine(text="分级: TR4"),
            knowledge_agent=FakeKnowledgeAgent(chunks=[chunk]),
        )
        response = AgentResponse(agent_name="thyroid_specialist", organ=Organ.THYROID)

        specialist._enrich_clinical_attributes(response, "分级: TR4", [chunk])

        assert response.differential_diagnoses[0].disease_name == "乳头状癌"


class TestEnrichmentRobustness:
    def test_failure_is_swallowed(self, monkeypatch):
        """增强能力失败不得影响诊断主流程。"""
        specialist = ThyroidSpecialistAgent(
            inference_engine=FakeInferenceEngine(text="分级: TR4"),
            knowledge_agent=FakeKnowledgeAgent(chunks=make_chunks(2)),
        )
        response = AgentResponse(agent_name="thyroid_specialist", organ=Organ.THYROID)

        import app.agents.specialist.base as base_module

        def _boom(*_args, **_kwargs):
            raise RuntimeError("extraction down")

        monkeypatch.setattr(base_module, "extract_udaf_features", _boom)
        specialist._enrich_clinical_attributes(response, "低回声", [])

        assert response.malignancy_risk is None
        assert response.measurement is None

    def test_falls_back_to_chunk_text(self):
        """LLM 输出无特征时回退到检索到的知识 chunk 文本。"""
        from tests.unit.agt.conftest import make_chunk

        chunk = make_chunk(
            chunk_id="k1", content="甲状腺结节边界不清、微钙化、后方声影"
        )
        specialist = ThyroidSpecialistAgent(
            inference_engine=FakeInferenceEngine(text="分级: TR3"),
            knowledge_agent=FakeKnowledgeAgent(chunks=[chunk]),
        )
        response = AgentResponse(agent_name="thyroid_specialist", organ=Organ.THYROID)

        specialist._enrich_clinical_attributes(response, "分级: TR3", [chunk])

        assert response.posterior_acoustic == PosteriorAcoustic.SHADOWING
        assert response.malignancy_risk is not None
