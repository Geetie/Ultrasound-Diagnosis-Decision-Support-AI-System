"""专科智能体基类的分支补测（quality-debt，核心模块 app/agents；76.1% -> 目标高位）。

这一层的价值全在**降级与阈值**上：专科智能体是诊断链路里最容易出问题的一环
（LLM 不可用、返回体解析不了、知识库检索空），而它的设计原则是
"任何一环失败都不许把 dispatcher 拖死"——所以每条失败路径都必须真的走到，
并且要断言它给出的是"要求人工复核"而不是一个看起来正常的结论。

覆盖：
- `__init__` 的五条参数校验（含病种阈值越界）；
- `invoke` 的解析失败回退（confidence=0、content 带 parse_failed、trace 有标签），
  以及"器官级 trace 为空时直接用公共 trace"这条合并分支；
- `health_check` 的双通道降级（知识库挂 / 推理挂 / 全挂）；
- `_check_disease_threshold` 的两级回退（病种级优先、器官级兜底、都无则不检查）
  与 confidence=0 的跳过语义；
- `_load_default_prompt` 的三级降级（organ 属性 → get_organ() → 类名启发式）与缺文件报错；
- 占位智能体（Phase 0A 兼容）的三个方法。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.protocols import AgentContext
from app.agents.specialist import base as base_module
from app.agents.specialist.base import (
    AgentResponse,
    PlaceholderSpecialistAgent,
    RAGSpecialistAgent,
)
from app.inference.protocols import InferenceResponse
from app.models.schemas import Organ


class _StubSpecialist(RAGSpecialistAgent):
    """把四步里除被测分支外的部分全部固定住，避免测试依赖真实检索与 LLM。"""

    organ = Organ.THYROID

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("prompt_template", "stub prompt")
        kwargs.setdefault("inference_engine", MagicMock())
        kwargs.setdefault("knowledge_agent", MagicMock())
        super().__init__(**kwargs)
        self.parse_should_fail = False
        self.parsed_response: AgentResponse | None = None

    def get_organ(self) -> Organ:
        return self.organ

    def get_grading_system(self):
        return MagicMock()

    async def _retrieve_chunks(self, context, organ):
        return [MagicMock(chunk_id="c1")]

    async def _call_llm_response(self, context, prompt, *, structured=False):
        # 基类 `invoke` 走 `_call_llm_response`（保留 finish_reason 供截断判定）。
        # 分级抽取失败时的结构化重试也走这里 —— 本 stub 固定返回同一文本，
        # 因此重试必然仍抽不到（正是"降级"用例需要的语义）。
        return InferenceResponse(text="llm output", finish_reason="stop")

    def _parse_response(self, llm_text, chunks, *, grade_override=None):
        if self.parse_should_fail:
            raise RuntimeError("unparsable json")
        return self.parsed_response or AgentResponse(
            agent_name="stub",
            organ=self.organ,
            content="ok",
            confidence=0.9,
            evidence_chunk_ids=["c1"],
            reasoning_trace=[],
        )

    def _enrich_clinical_attributes(self, response, llm_text, chunks):
        return None


def _context(imaging_metadata=None) -> AgentContext:
    """用**真实** AgentContext，不要用 MagicMock。

    第一版我用 `MagicMock()` 当上下文，结果 `_build_prompt` 对元数据做字符串
    拼接时抛 `TypeError: expected str instance, MagicMock found`——
    `invoke` 内部也要拼 prompt，于是相关用例连带失败。
    上下文是 pydantic 模型，构造它本来就没有成本。
    """
    return AgentContext(
        request_id="req-1",
        session_id="sess-1",
        organ=Organ.THYROID,
        user_query="甲状腺低回声结节",
        imaging_metadata=imaging_metadata,
    )


class TestConstructorValidation:
    def test_base_class_cannot_be_instantiated_directly(self):
        """抽象基类不可直接实例化：三个抽象方法必须由器官级子类实现。

        （我第一版直接用 `RAGSpecialistAgent(...)` 去测 `__init__` 校验，
        结果拿到的是抽象类 TypeError —— 校验根本没跑到。）
        """
        with pytest.raises(TypeError):
            RAGSpecialistAgent(inference_engine=MagicMock(), knowledge_agent=MagicMock())

    def test_rejects_none_inference_engine(self):
        with pytest.raises(ValueError, match="inference_engine must not be None"):
            _StubSpecialist(inference_engine=None)

    def test_rejects_none_knowledge_agent(self):
        with pytest.raises(ValueError, match="knowledge_agent must not be None"):
            _StubSpecialist(knowledge_agent=None)

    def test_rejects_non_positive_rag_top_k(self):
        with pytest.raises(ValueError, match="rag_top_k must be positive"):
            _StubSpecialist(rag_top_k=0)

    def test_rejects_non_positive_max_tokens(self):
        with pytest.raises(ValueError, match="max_tokens must be positive"):
            _StubSpecialist(max_tokens=0)

    def test_rejects_out_of_range_organ_threshold(self):
        with pytest.raises(ValueError, match="organ_threshold must be in"):
            _StubSpecialist(organ_threshold=1.5)

    def test_rejects_out_of_range_disease_threshold(self):
        """病种阈值写错的话，该病种会永远触发 needs_review 或永远不触发——必须挡在构造期。"""
        with pytest.raises(ValueError, match="disease_thresholds"):
            _StubSpecialist(disease_thresholds={"thyroid_nodule": 1.2})

    def test_accepts_boundary_thresholds(self):
        """0.0 与 1.0 是合法边界（校验是闭区间）。"""
        agent = _StubSpecialist(organ_threshold=0.0, disease_thresholds={"x": 1.0})

        assert agent is not None


class TestInvokeDegradation:
    async def test_parse_failure_degrades_without_raising(self):
        """解析失败必须降级返回，不能把异常抛给 dispatcher（否则一次坏响应拖垮整条诊断）。"""
        agent = _StubSpecialist()
        agent.parse_should_fail = True

        response = await agent.invoke(_context())

        assert response.confidence == 0.0
        # A.59：正文中文可读（英文技术串只留 trace），且降级**必须转人工**
        assert "判读结果无法解析" in response.content
        assert "已转人工判读" in response.content
        assert "parse_failed" not in response.content
        assert response.needs_review is True
        assert any(tag.startswith("parse_failed:") for tag in response.reasoning_trace)
        assert response.latency_ms >= 0.0

    async def test_public_trace_used_when_organ_trace_empty(self):
        """器官级 trace 为空时直接采用公共 trace（否则整条链路看不出走过哪些步骤）。"""
        agent = _StubSpecialist()

        response = await agent.invoke(_context())

        assert response.reasoning_trace, "公共 trace 必须被带上"
        assert any(tag.startswith("route ->") for tag in response.reasoning_trace)
        assert response.organ is Organ.THYROID
        assert response.agent_name

    async def test_organ_trace_comes_before_public_trace(self):
        """器官级 trace 在前、公共 trace 在后：顺序反了会让人读错推理步骤。"""
        agent = _StubSpecialist()
        agent.parsed_response = AgentResponse(
            agent_name="stub",
            organ=Organ.THYROID,
            content="ok",
            confidence=0.9,
            evidence_chunk_ids=["c1"],
            reasoning_trace=["organ-level-step"],
        )

        response = await agent.invoke(_context())

        assert response.reasoning_trace[0] == "organ-level-step"
        assert any(tag.startswith("route ->") for tag in response.reasoning_trace)


class TestHealthCheckDegradation:
    async def test_healthy_when_both_channels_ok(self):
        agent = _StubSpecialist()
        agent._knowledge.health_check = AsyncMock(return_value=True)
        agent._inference.health_check = AsyncMock(return_value=True)

        assert await agent.health_check() is True

    async def test_rag_down_still_healthy_if_llm_up(self):
        """知识库挂了但推理在：仍算可用（返回的是 `或`，不是 `且`）。"""
        agent = _StubSpecialist()
        agent._knowledge.health_check = AsyncMock(side_effect=RuntimeError("rag down"))
        agent._inference.health_check = AsyncMock(return_value=True)

        assert await agent.health_check() is True

    async def test_llm_down_still_healthy_if_rag_up(self):
        agent = _StubSpecialist()
        agent._knowledge.health_check = AsyncMock(return_value=True)
        agent._inference.health_check = AsyncMock(side_effect=RuntimeError("llm down"))

        assert await agent.health_check() is True

    async def test_unhealthy_when_both_channels_down(self):
        agent = _StubSpecialist()
        agent._knowledge.health_check = AsyncMock(side_effect=RuntimeError("rag down"))
        agent._inference.health_check = AsyncMock(side_effect=RuntimeError("llm down"))

        assert await agent.health_check() is False

    async def test_close_releases_prompt_template(self):
        """close 要清掉提示词缓存：常驻进程里不会沿用上一代模板。"""
        agent = _StubSpecialist(prompt_template="some prompt")
        assert agent._prompt_template

        await agent.close()

        assert agent._prompt_template == ""


class TestDiseaseThreshold:
    def _response(self, confidence: float) -> AgentResponse:
        return AgentResponse(
            agent_name="stub",
            organ=Organ.THYROID,
            content="ok",
            confidence=confidence,
            reasoning_trace=[],
        )

    def test_zero_confidence_is_skipped(self):
        """confidence=0 表示已降级，降级本身已要求人工介入，不再叠加 needs_review。"""
        agent = _StubSpecialist(organ_threshold=0.9)
        response = self._response(0.0)

        agent._check_disease_threshold(response, "thyroid_nodule")

        assert response.needs_review is False
        assert response.reasoning_trace == []

    def test_disease_threshold_takes_precedence(self):
        """病种级阈值优先于器官级：两者都配时应按更严的病种阈值判定。"""
        agent = _StubSpecialist(
            organ_threshold=0.5, disease_thresholds={"thyroid_nodule": 0.9}
        )
        response = self._response(0.7)

        agent._check_disease_threshold(response, "thyroid_nodule")

        assert response.needs_review is True
        assert any("below_disease_threshold" in tag for tag in response.reasoning_trace)

    def test_organ_threshold_used_when_disease_unknown(self):
        agent = _StubSpecialist(organ_threshold=0.9)
        response = self._response(0.7)

        agent._check_disease_threshold(response, "unknown_disease")

        assert response.needs_review is True
        assert any("below_organ_threshold" in tag for tag in response.reasoning_trace)

    def test_no_threshold_configured_is_noop(self):
        """没配阈值就不检查（向后兼容：既有子类不该被凭空要求复核）。"""
        agent = _StubSpecialist()
        response = self._response(0.1)

        agent._check_disease_threshold(response, "thyroid_nodule")

        assert response.needs_review is False

    def test_confidence_above_threshold_untouched(self):
        agent = _StubSpecialist(organ_threshold=0.5)
        response = self._response(0.9)

        agent._check_disease_threshold(response, None)

        assert response.needs_review is False
        assert response.reasoning_trace == []


class TestLoadDefaultPrompt:
    """提示词文件加载的三级降级与缺文件报错。

    用 tmp_path 顶替真实提示词目录：既避免依赖仓库里现存文件，
    也能真的把"文件不存在"这条错误路径走到。

    三级来源分别是：类属性 `organ` -> `get_organ()` -> 类名启发式。
    用 `organ = None` 把第一级"摘掉"，而不是 `delattr`——后者对从基类继承来的
    属性无效（第一版就栽在这：AttributeError 直接让用例报错）。
    """

    def test_reads_prompt_by_organ_attribute(self, monkeypatch, tmp_path):
        (tmp_path / "thyroid.md").write_text("甲状腺提示词", encoding="utf-8")
        monkeypatch.setattr(base_module, "_PROMPTS_DIR", tmp_path)
        agent = _StubSpecialist()

        assert agent._load_default_prompt() == "甲状腺提示词"

    def test_missing_prompt_file_raises_with_path(self, monkeypatch, tmp_path):
        """缺文件必须报错（而不是返回空串——空提示词会让 LLM 自由发挥）。"""
        monkeypatch.setattr(base_module, "_PROMPTS_DIR", tmp_path)
        agent = _StubSpecialist()

        with pytest.raises(FileNotFoundError, match="prompt template not found"):
            agent._load_default_prompt()

    def test_falls_back_to_get_organ_when_no_organ_attribute(self, monkeypatch, tmp_path):
        (tmp_path / "liver.md").write_text("肝脏提示词", encoding="utf-8")
        monkeypatch.setattr(base_module, "_PROMPTS_DIR", tmp_path)

        class _OrganlessAgent(_StubSpecialist):
            organ = None

            def get_organ(self) -> Organ:
                return Organ.LIVER

        assert _OrganlessAgent()._load_default_prompt() == "肝脏提示词"

    def test_falls_back_to_class_name_heuristic(self, monkeypatch, tmp_path):
        """get_organ() 未实现时按类名推目录名（Phase 0A 的过渡路径）。"""
        (tmp_path / "cardiac.md").write_text("心脏提示词", encoding="utf-8")
        monkeypatch.setattr(base_module, "_PROMPTS_DIR", tmp_path)

        class CardiacSpecialistAgent(_StubSpecialist):
            organ = None

            def get_organ(self) -> Organ:
                raise NotImplementedError

        assert CardiacSpecialistAgent()._load_default_prompt() == "心脏提示词"


class TestPlaceholderSpecialist:
    """Phase 0A 兼容：真实 specialist 未接线时由占位类顶上。"""

    def test_capability_tag_marks_placeholder(self):
        agent = PlaceholderSpecialistAgent(Organ.LIVER)

        assert agent.get_capability_tags() == ["liver_placeholder"]

    async def test_health_check_is_always_true(self):
        """占位类永不失败：否则 Phase 0A 的 dispatcher 集成测试会被健康检查拦住。"""
        assert await PlaceholderSpecialistAgent(Organ.THYROID).health_check() is True

    async def test_invoke_returns_zero_confidence_pending_marker(self):
        agent = PlaceholderSpecialistAgent(Organ.CARDIAC)

        response = await agent.invoke(_context())

        assert response.confidence == 0.0
        assert "specialist_pending_implementation" in response.content
        assert response.organ is Organ.CARDIAC

    async def test_close_is_noop(self):
        assert await PlaceholderSpecialistAgent(Organ.THYROID).close() is None


class TestBuildPromptImagingSection:
    def test_prompt_is_non_empty_without_metadata(self):
        agent = _StubSpecialist()

        prompt = agent._build_prompt(_context(imaging_metadata=None), [], [])

        assert isinstance(prompt, str) and prompt

    def test_imaging_metadata_is_appended(self):
        """有影像元数据时必须带进提示词：医生填的切面/部位信息会影响分级。"""
        agent = _StubSpecialist()

        prompt = agent._build_prompt(
            _context(imaging_metadata={"plane": "transverse", "probe": "linear"}), [], []
        )

        assert "transverse" in prompt
        assert "linear" in prompt
