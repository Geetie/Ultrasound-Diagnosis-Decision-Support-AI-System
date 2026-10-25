"""A.88 配置接线④：``dispatch_all`` 的**契约测试 + 门控开关 + dry-run 路径**。

## 为什么是"契约 + 门控 + dry-run"而不是直接切默认（诚实交付）

`dispatch_all()` 此前**零生产调用方**（《架构真相》§3.3「多专科并行」）。
但把它硬接进去并不安全，理由有两条硬约束：

1. **GPU 显存**：本机 48GB，SKILL 记录在跑的组合已占 ≈45.4GB
   （8002 主生成 0.46 + 判官 0.36 + 嵌入 6.8 等常驻）。`dispatch_all` 是
   `asyncio.gather` 真并发 → 同一时刻多个 specialist 各自打 vLLM，
   峰值显存按并发数叠加，**极易 OOM 并连带打挂整台机器的推理服务**。
2. **语义冲突**：多器官编排器（`multi_organ_orchestrator.py`）的超时预算是
   "串行 + 单器官 30s + 全局 90s"。并行调度只覆盖 `dispatcher.invoke` 那一段，
   不含**每个器官各自的融合/SafetyGateway/写终态**（那段在
   `diagnosis_pipeline.run_diagnosis_pipeline`，本任务不可改）。若在编排器里
   用 `dispatch_all` 替换串行管线，等于凭空造出第二条**不完整**的判读链
   （没有融合、没有安全门、没有写库），与"只改接线不改语义"直接冲突。

因此本次交付：**契约测试 + 默认关的门控开关 + 可运行的 dry-run 路径**，
并在 `run_multi_organ_diagnosis` 里记录"这条路径当前是否可用/为什么没开"
（可观测，不静默）。真机验证显存与耗时后再决定是否开默认。

## 本文件钉住的

* 契约：`dispatch_all` 保序、空输入、失败降级、共享同一 context、**不做聚合**、
  不调用未请求的器官；同器官重复出现时**真的调用两次**（冲突检测要求同器官
  ≥2 条独立结论，这是它存在的意义）。
* 门控：开关关闭 → `dispatch_organs_parallel` **拒绝真跑**并给出原因，
  绝不偷偷发 LLM 请求。
* dry-run：不开门控也能跑，产出计划（器官、调用数、门控状态、风险提示），
  不发任何 LLM 请求。
* 反证腿：①把单器官端点接上 `dispatch_all` → 源码守卫红；②把门控默认改成
  True → 开关用例红；③把 dry-run 改成真调用 → "dry-run 不发 LLM"用例红。
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from app.agents.dispatcher import DispatcherAgentImpl, dispatch_all
from app.agents.protocols import AgentContext
from app.models.schemas import Organ
from app.services import multi_organ_orchestrator as orch


def _ctx(organ: Organ = Organ.THYROID) -> AgentContext:
    return AgentContext(request_id="r1", session_id="s1", organ=organ, user_query="q")


class _SlowSpecialist:
    """记录并发度的 specialist 替身（sleep 一次让 gather 真的重叠）。"""

    def __init__(self, organ: Organ, delay: float = 0.01, exc: Exception | None = None):
        self.organ = organ
        self.delay = delay
        self.exc = exc
        self.calls = 0
        self.peak = 0

    async def invoke(self, context: AgentContext):
        from app.agents.protocols import AgentResponse

        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return AgentResponse(
            agent_name=f"{self.organ.value}_spec",
            organ=self.organ,
            content=f"{self.organ.value} ok",
            confidence=0.75,
            evidence_chunk_ids=[f"{self.organ.value}_c1"],
        )

    def get_capability_tags(self):
        return [self.organ.value]

    async def health_check(self) -> bool:
        return True


class _ConcurrencyProbe:
    """统计同时在飞的调用数（证明 dispatch_all 是**真并发**）。"""

    def __init__(self):
        self.now = 0
        self.peak = 0

    def enter(self):
        self.now += 1
        self.peak = max(self.peak, self.now)

    def leave(self):
        self.now -= 1


def _probe_specialist(organ: Organ, probe: _ConcurrencyProbe):
    """返回一个会在 invoke 里更新 probe 的 specialist。"""

    class _Spec:
        async def invoke(self, context):
            from app.agents.protocols import AgentResponse

            probe.enter()
            try:
                await asyncio.sleep(0.02)
            finally:
                probe.leave()
            return AgentResponse(
                agent_name=f"{organ.value}_spec",
                organ=organ,
                content="ok",
                confidence=0.8,
            )

        def get_capability_tags(self):
            return [organ.value]

        async def health_check(self):
            return True

    return _Spec()


# ----------------------------------------------------------------------
# 1) 契约
# ----------------------------------------------------------------------


class TestDispatchAllContract:
    @pytest.mark.asyncio
    async def test_preserves_requested_order(self):
        specialists = {o: _SlowSpecialist(o, delay=0.01 if o is Organ.LIVER else 0.0)
                       for o in (Organ.THYROID, Organ.LIVER, Organ.CARDIAC)}
        dispatcher = DispatcherAgentImpl(specialists=specialists)
        responses = await dispatch_all(
            dispatcher, _ctx(), [Organ.CARDIAC, Organ.THYROID, Organ.LIVER]
        )
        assert [r.organ for r in responses] == [Organ.CARDIAC, Organ.THYROID, Organ.LIVER], (
            "必须按入参顺序返回：编排器/融合层按器官对齐，乱序会让结论错配"
        )

    @pytest.mark.asyncio
    async def test_is_genuinely_concurrent(self):
        """反证腿：把 gather 改成串行 for-await，本用例即红（peak 会恒为 1）。"""
        probe = _ConcurrencyProbe()
        dispatcher = DispatcherAgentImpl(
            specialists={
                o: _probe_specialist(o, probe)
                for o in (Organ.THYROID, Organ.LIVER, Organ.CARDIAC)
            }
        )
        await dispatch_all(dispatcher, _ctx(), [Organ.THYROID, Organ.LIVER, Organ.CARDIAC])
        assert probe.peak == 3

    @pytest.mark.asyncio
    async def test_does_not_aggregate(self):
        """`dispatch_all` 返回**原始响应列表**（聚合由调用方决定）。"""
        specialists = {o: _SlowSpecialist(o) for o in (Organ.THYROID, Organ.LIVER)}
        dispatcher = DispatcherAgentImpl(specialists=specialists)
        responses = await dispatch_all(dispatcher, _ctx(), [Organ.THYROID, Organ.LIVER])
        assert [r.content for r in responses] == ["thyroid ok", "liver ok"]
        # 聚合会把 content 拼成一段并改 agent_name；这里必须还是各自的
        assert responses[0].agent_name == "thyroid_spec"

    @pytest.mark.asyncio
    async def test_failure_degrades_without_affecting_siblings(self):
        specialists = {
            Organ.THYROID: _SlowSpecialist(Organ.THYROID, exc=ConnectionError("down")),
            Organ.LIVER: _SlowSpecialist(Organ.LIVER),
        }
        dispatcher = DispatcherAgentImpl(specialists=specialists)
        responses = await dispatch_all(dispatcher, _ctx(), [Organ.THYROID, Organ.LIVER])
        assert responses[0].confidence == 0.0
        assert responses[0].needs_review is True
        assert responses[1].confidence == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_empty_organs_returns_empty_without_calls(self):
        specialists = {Organ.THYROID: _SlowSpecialist(Organ.THYROID)}
        dispatcher = DispatcherAgentImpl(specialists=specialists)
        assert await dispatch_all(dispatcher, _ctx(), []) == []
        assert specialists[Organ.THYROID].calls == 0

    @pytest.mark.asyncio
    async def test_same_organ_twice_really_calls_twice(self):
        """**同器官多专科**场景：冲突检测要求同器官 ≥2 条独立结论。

        这是 dispatch_all 唯一能喂饱 `GRADING_CONFLICT` 的用法之一，
        所以"重复器官被去重"是**错误**行为，必须真的调用两次。
        """
        specialists = {Organ.THYROID: _SlowSpecialist(Organ.THYROID)}
        dispatcher = DispatcherAgentImpl(specialists=specialists)
        responses = await dispatch_all(dispatcher, _ctx(), [Organ.THYROID, Organ.THYROID])
        assert len(responses) == 2
        assert specialists[Organ.THYROID].calls == 2

    @pytest.mark.asyncio
    async def test_shares_one_context_object(self):
        """所有 specialist 收到**同一个** context（证据按器官顺序累积的契约）。"""
        seen: list[int] = []
        specialists = {}

        for organ in (Organ.THYROID, Organ.LIVER):
            class _Spec:
                def __init__(self, o):
                    self.o = o

                async def invoke(self, context):
                    from app.agents.protocols import AgentResponse

                    seen.append(id(context))
                    return AgentResponse(
                        agent_name=f"{self.o.value}_spec",
                        organ=self.o,
                        content="ok",
                        confidence=0.8,
                    )

                def get_capability_tags(self):
                    return [self.o.value]

                async def health_check(self):
                    return True

            specialists[organ] = _Spec(organ)

        dispatcher = DispatcherAgentImpl(specialists=specialists)
        await dispatch_all(dispatcher, _ctx(), [Organ.THYROID, Organ.LIVER])
        assert len(set(seen)) == 1


# ----------------------------------------------------------------------
# 2) 门控开关（默认关）+ 拒绝真跑
# ----------------------------------------------------------------------


class TestGateDefaultsOff:
    def test_setting_defaults_false(self):
        from app.config.settings import Settings

        assert Settings().multi_organ_parallel_dispatch_enabled is False

    def test_dry_run_works_with_gate_off(self):
        """dry-run 不需要开门控（它不发 LLM 请求），但必须如实报告门控状态。"""
        plan = orch.parallel_dispatch_plan([Organ.THYROID, Organ.LIVER])
        assert plan["mode"] == "dry_run"
        assert plan["gate_enabled"] is False
        assert plan["organs"] == ["thyroid", "liver"]
        assert plan["estimated_llm_calls"] == 2

    @pytest.mark.asyncio
    async def test_real_dispatch_refused_when_gate_off(self):
        """反证腿：把门控判断删掉 → 这里会真的打 specialist（calls>0）即红。"""
        specialist = _SlowSpecialist(Organ.THYROID)
        dispatcher = DispatcherAgentImpl(specialists={Organ.THYROID: specialist})

        result = await orch.dispatch_organs_parallel(
            session_id="s1",
            organs=[Organ.THYROID],
            dispatcher=dispatcher,
            dry_run=False,
            gate_enabled=False,
        )

        assert result["status"] == "disabled"
        assert result["reason"], "拒绝必须给出原因（不许静默不发请求）"
        assert specialist.calls == 0

    @pytest.mark.asyncio
    async def test_real_dispatch_runs_when_gate_on(self):
        specialist = _SlowSpecialist(Organ.THYROID)
        dispatcher = DispatcherAgentImpl(specialists={Organ.THYROID: specialist})

        result = await orch.dispatch_organs_parallel(
            session_id="s1",
            organs=[Organ.THYROID],
            dispatcher=dispatcher,
            dry_run=False,
            gate_enabled=True,
        )

        assert result["status"] == "dispatched"
        assert specialist.calls == 1
        assert result["responses"][0]["organ"] == "thyroid"


# ----------------------------------------------------------------------
# 3) dry-run 计划内容（可执行、可观测、有真实原因）
# ----------------------------------------------------------------------


    @pytest.mark.asyncio
    async def test_dry_run_never_calls_specialists(self):
        """反证腿：把 ``dry_run`` 分支删掉（改成一律真跑）→ 本用例即红。"""
        specialist = _SlowSpecialist(Organ.THYROID)
        dispatcher = DispatcherAgentImpl(specialists={Organ.THYROID: specialist})

        result = await orch.dispatch_organs_parallel(
            session_id="s1",
            organs=[Organ.THYROID],
            dispatcher=dispatcher,
            dry_run=True,
            gate_enabled=True,  # 即使门控打开，dry-run 也不许真跑
        )

        assert result["status"] == "dry_run"
        assert specialist.calls == 0


class TestDryRunPlanContent:
    def test_recommends_against_enabling_and_says_why(self):
        """计划里必须写明"为什么现在不建议开"（显存 + 语义），不能只报一个布尔。"""
        plan = orch.parallel_dispatch_plan([Organ.THYROID, Organ.LIVER, Organ.CARDIAC])
        assert plan["recommended"] is False
        reasons = " ".join(plan["reasons"])
        assert "显存" in reasons
        assert "90" in reasons or "预算" in reasons

    def test_reports_missing_specialists(self):
        """注册不全 → 明确列出缺哪些器官（否则真跑会静默降级成占位响应）。"""
        dispatcher = DispatcherAgentImpl(
            specialists={Organ.THYROID: _SlowSpecialist(Organ.THYROID)}
        )
        plan = orch.parallel_dispatch_plan(
            [Organ.THYROID, Organ.LIVER], dispatcher=dispatcher
        )
        assert plan["missing_specialists"] == ["liver"]

    def test_empty_organs_is_dispatchable_noop(self):
        plan = orch.parallel_dispatch_plan([])
        assert plan["estimated_llm_calls"] == 0
        assert plan["organs"] == []


# ----------------------------------------------------------------------
# 4) 接线位置守卫：**不得**接到单器官端点 / 管线
# ----------------------------------------------------------------------


class TestNotWiredToSingleOrganPaths:
    def test_single_organ_endpoint_does_not_use_dispatch_all(self):
        from app.api.v1 import diagnosis as diagnosis_api

        source = inspect.getsource(diagnosis_api)
        assert "dispatch_all" not in source, (
            "单器官端点不得使用 dispatch_all：会与'单部位判读'语义冲突，"
            "并把 LLM 成本翻倍（同一次判读要出两份结论）"
        )

    def test_diagnosis_pipeline_untouched(self):
        from app.services import diagnosis_pipeline

        source = inspect.getsource(diagnosis_pipeline)
        assert "dispatch_all" not in source, (
            "单请求管线必须保持'单专科'语义（《架构真相》§0 的真实链路）"
        )

    def test_multi_organ_records_advisory_plan(self):
        """多器官会话里记录该路径可用性（可观测），且**默认不发 LLM 请求**。"""
        source = inspect.getsource(orch.run_multi_organ_diagnosis)
        assert "parallel_dispatch_plan" in source
        # 生产主路径只允许"计划"：不许**调用**真并发入口（只有注释可提及）。
        # 反证腿：把 `await dispatch_organs_parallel(...)` 写进主循环即红。
        assert "await dispatch_organs_parallel(" not in source
        assert "dispatch_all(" not in source
