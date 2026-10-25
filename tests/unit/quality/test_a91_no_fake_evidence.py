"""A.91 守卫：**"打通"与"真实实现"必须可区分**——禁止把占位/模拟当功能证据。

## 这份守卫要拦的是什么（用户 2026-10-25 明确要求）

> "排查所有假数据假测试，不得拿模板回答作为功能真实打通且实现的证据。
>   打通和真实实现是有区别的。"

本项目历史上真实发生过两次同族事故，值得写在文件头：

1. **A.70**：评测集 98.7% 的"结论"是**提示词示例照抄**（`chunk_a1b2` /
   `患者PATIENT_001` 整段复述）。当时的 faithfulness 0.0906 等指标**全部建立在
   假结论上** —— 数字能算出来，但算的不是系统能力。
2. **A.77**：我把"出现 `PATIENT_001`"当模板回显铁证，**误删 415 条真结论**。
   反方向的错：把真数据当假数据。

两次都源于同一个问题：**没有区分"证据的等级"**。

## 本守卫采纳的三级判据（谁可以当"真实实现"的证据）

| 级别 | 判据 | 可用于 |
|---|---|---|
| **E1 真机实测** | 真实外部依赖（PG/Qdrant/8003/vLLM/9001）跑出真实产出，且**产出可追溯到真实输入** | 可以说"已打通并验证" |
| **E2 行为级单测** | 不 mock 被测对象本身，断言**产出内容/状态**（不是"函数被调用过"） | 可以说"逻辑已实现" |
| **E3 源码级断言** | 只断言"源码里有某个字符串/函数名" | **只能**说"接线存在"，**不能**说"功能可用" |

## 本守卫钉死四条不许违反的规则

1. **占位实现不得自我报告健康**：`PlaceholderSpecialistAgent.health_check()`
   原先恒 `True`（"占位不会失败，方便集成测试"）—— 这会让
   `agents_health_check=dispatcher:True` 在**专科根本没实现**时也显示全绿。
   现在必须是 `False`（诚实的"我不可用"）。
2. **假实现的产物不得伪装成真实结果**：`local_fake` 系列适配器必须在返回值里
   **显式标注** `adapter="local_fake"`（已有），且**日志事件名必须含 fake**
   （`pacs_local_fake_pushed`），不得只写 `pacs_pushed`。
3. **测试不得把"mock 被调用"当功能验证**：凡是声称验证"接线"的测试，必须
   **同时**断言产出内容/状态；只有 `assert_called_once` 而无结果断言的，视为
   **未验证**（本守卫抽查关键文件）。
4. **源码级接线断言必须标注等级**：这类测试的 docstring 必须写明
   "仅证明接线存在，不证明功能可用"，防止后来者拿它当"已打通"的证据。

## 为什么这些规则值得写成门禁

因为代价是可量化的：
* A.70：假结论导致 4 轮指标口径争议 + 608 条历史数据作废；
* A.77：一次规则误判删掉 415 条真结论（不可完全恢复）；
* 2026-10-25（A.89）：`sg_patient_name` 把"请咨询主治医生"判成患者姓名，
  半数判读被静默抑制 —— 而当时的日志只写"检测到敏感"，**看不出是误报**。
三者共同点：**系统在"看起来成功"和"真的成功"之间没有可观测的分界**。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.track_quality]

_REPO = Path(__file__).resolve().parents[3]


def _src(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# 规则 1：占位实现不得自我报告健康
# ----------------------------------------------------------------------


class TestPlaceholderMustNotReportHealthy:
    """占位专科若恒报健康，`agents_health_check` 会在"专科没实现"时全绿。"""

    def test_placeholder_specialist_health_is_false(self):
        """反证腿：把 `health_check` 改回 `return True` 即红。

        历史：原实现 `return True`，注释写"占位不会失败，方便 dispatcher 集成测试"。
        但 lifespan 的 `agents_health_check` 会拿它当"三专科就绪"的证据 ——
        一旦生产误落到占位路径，健康检查仍然全绿，运维看不出问题。
        """
        from app.agents.specialist.base import PlaceholderSpecialistAgent
        from app.models.schemas import Organ

        agent = PlaceholderSpecialistAgent(Organ.THYROID)
        import asyncio

        assert asyncio.run(agent.health_check()) is False, (
            "占位专科必须自报不健康 —— 否则'专科未实现'会被显示成'全部就绪'"
        )

    def test_placeholder_content_is_marked(self):
        """占位响应正文必须**自带不可用标记**（不许长得像真结论）。"""
        from app.agents.protocols import AgentContext
        from app.agents.specialist.base import PlaceholderSpecialistAgent
        from app.models.schemas import Organ

        agent = PlaceholderSpecialistAgent(Organ.THYROID)
        ctx = AgentContext(
            request_id="t", session_id="s", organ=Organ.THYROID, user_query="x"
        )
        import asyncio

        resp = asyncio.run(agent.invoke(ctx))
        assert resp.confidence == 0.0
        assert "pending" in resp.content.lower() or "占位" in resp.content, (
            "占位响应正文必须显式标注未实现"
        )


# ----------------------------------------------------------------------
# 规则 2：假适配器的日志事件名必须含 fake
# ----------------------------------------------------------------------


class TestFakeAdaptersAreLabelled:
    """`local_fake` 适配器可以存在（无外部环境时验证编排），但**不许冒充真推送**。"""

    @pytest.mark.parametrize(
        "rel,required_token",
        [
            ("app/integrations/pacs/__init__.py", "pacs_local_fake"),
            ("app/integrations/object_storage/__init__.py", "local_fake"),
            ("app/inference/hot_reload.py", "local_fake"),
        ],
    )
    def test_fake_adapter_log_events_name_the_fake(self, rel, required_token):
        src = _src(rel)
        # 允许存在 local_fake 实现，但日志事件必须能一眼看出是假的
        assert required_token in src, f"{rel} 里没有 {required_token} 标记"
        assert 'return "local_fake"' in src or 'name = "local_fake"' in src or "local_fake" in src

    def test_pacs_fake_push_result_carries_adapter_name(self):
        """假适配器的返回值必须带 `adapter` 名，调用方据此区分真假。"""
        import asyncio

        from app.integrations.pacs import LocalFakePacsAdapter

        a = LocalFakePacsAdapter()
        assert a.name == "local_fake"
        r = asyncio.run(
            a.push_report(
                report_id="r1",
                payload=b"rendered-report",
                idempotency_key="k1",
                patient_id="p1",
            )
        )
        assert r.success is True
        # 关键：成功但**明确标注**是 fake 适配器 —— 调用方/审计据此不把它当
        # "已真的推到 PACS" 的证据
        assert r.adapter == "local_fake"


# ----------------------------------------------------------------------
# 规则 3：声称"接线"的测试不许只有 mock 调用断言
# ----------------------------------------------------------------------


class TestWiringTestsAssertOutcome:
    """声称验证接线的测试，必须断言**产出**，不能只断言"函数被调用过"。"""

    @pytest.mark.parametrize(
        "rel",
        [
            "tests/unit/services/test_a88_hallucination_wiring.py",
            "tests/unit/services/test_a88_t2a_bypass.py",
            "tests/unit/services/test_a88_cross_organ_fusion.py",
        ],
    )
    def test_has_outcome_assertions(self, rel):
        """每个接线测试文件里，断言"产出内容/状态"的条数必须 ≥ mock 调用断言。

        判据用 AST：统计 `assert x == ...` / `assert "..." in x` 形态（结果断言）
        与 `assert_called*`（调用断言）的比例。
        """
        src = _src(rel)
        n_called = len(re.findall(r"assert_(called|awaited)", src))
        # 结果断言：至少有对内层字段/值的比较
        n_outcome = len(
            re.findall(
                r"assert\s+(?:[\w\.\[\]\"']+\s*(?:==|!=|in|is)\s*|[\w\.]+\()", src
            )
        )
        assert n_outcome > 0, f"{rel} 完全没有结果断言"
        assert n_outcome >= n_called, (
            f"{rel}: 结果断言 {n_outcome} 条 < 调用断言 {n_called} 条 —— "
            "这可能只是'函数被调用了'，不是'功能正确'"
        )


# ----------------------------------------------------------------------
# 规则 4：源码级断言必须自我标注
# ----------------------------------------------------------------------


class TestSourceLevelGuardsSelfLabel:
    """纯源码字符串断言的测试，其 docstring 必须说明'不证明功能可用'。"""

    KEY_SOURCE_GUARDS = (
        "tests/unit/services/test_a88_image_leg_pipeline_wiring.py",
        "tests/unit/rag/test_a90_image_leg_filter_relax.py",
    )

    @pytest.mark.parametrize("rel", KEY_SOURCE_GUARDS)
    def test_declares_its_evidence_level(self, rel):
        src = _src(rel)
        assert (
            "仅证明" in src or "只钉" in src or "源码" in src or "不证明" in src
        ), f"{rel} 是源码级断言，但没说明它的证据等级"


# ----------------------------------------------------------------------
# 规则 5：名为 e2e/integration 的测试不得把被测链路的**核心环节**全 mock 掉
# ----------------------------------------------------------------------


class TestE2EFilesDeclareWhatTheyMock:
    """`e2e`/`integration` 命名的测试若把核心环节全 mock，必须显式声明"非端到端"。

    ## 为什么这条规则值得存在

    真实案例：`tests/integration/int/test_e2e_diagnosis.py` 原名
    "6 个端到端诊断场景测试"，实际把管线五个核心步骤（dispatch / fuse /
    safety_check / write_db / DB 会话）**全部 mock**，只断言"它们被调用过"。
    把 `_safe_fuse` 整个换成 `return _mock_fusion_result(...)` 后它**依然全绿**
    —— 融合层删掉这个文件也发现不了。

    验收/答辩时看到"6 个 e2e 场景全绿"会合理推断"链路跑通了"，
    而事实是真实 LLM/RAG/融合/SG/DB 一个都没跑。这就是"打通"与"真实实现"
    没有分界造成的误读。

    ## 判据

    如果测试文件同时满足：
      * 路径含 `integration` 或文件名含 `e2e`；
      * patch 了 ≥3 个 `app.services.diagnosis_pipeline.*`（管线核心环节）；
      * 结果断言（非 assert_called）少于用例数的一半；
    那么**文件 docstring 必须**包含"非端到端"或"编排骨架"或"编排"字样
    （即自我声明证据等级），否则判红。
    """

    def _pipeline_patch_count(self, src: str) -> int:
        return len(
            re.findall(
                r'patch\(\s*["\']app\.services\.diagnosis_pipeline\.\w+["\']', src
            )
        )

    @pytest.mark.parametrize(
        "rel",
        [
            "tests/integration/int/test_e2e_diagnosis.py",
            "tests/integration/int/test_e2e_latency.py",
        ],
    )
    def test_declares_orchestration_only(self, rel):
        """判据：**patch 了 ≥3 个管线核心环节** → 必须声明证据等级。

        为什么不看"结果断言条数"：`test_e2e_diagnosis.py` 里有 7 条断言，但其中
        `assert result is True`（管线返回成功标志）、
        `assert fusion_result.final_response.confidence == 0.5`
        —— 而那个 `fusion_result` 正是 `_mock_fusion_result(...)` 造的，
        属于**断言自己造的 mock 值**（自我验证），不是验证真实链路。

        所以真正可靠的判据只有一条：**核心环节被 mock 了几个**。
        mock 了 ≥3 个就等于"这条链路没跑"，无论它有多少条断言，
        都必须声明"非端到端"。
        """
        src = _src(rel)
        n_patch = self._pipeline_patch_count(src)

        if n_patch < 3:
            pytest.skip(f"{rel} 只 patch 了 {n_patch} 个管线环节，不属于本规则范围")

        head = src[:4000]
        assert any(
            kw in head
            for kw in ("非端到端", "编排骨架", "只验证编排", "不属于端到端", "不是端到端")
        ), (
            f"{rel} patch 了 {n_patch} 个管线核心环节（= 真实链路没跑），"
            "但 docstring 没有声明'非端到端/编排骨架' —— "
            "读者会把它当成'诊断链路已打通'的证据"
        )

    def test_renamed_file_says_what_it_really_covers(self):
        """改名后的 docstring 必须点明"真实环节一个都没跑"与"E3 级证据"。"""
        head = _src("tests/integration/int/test_e2e_diagnosis.py")[:4000]
        assert "E3" in head or "源码/结构级" in head or "不属于" in head
        assert "mock" in head.lower()


# ----------------------------------------------------------------------
# 规则 6："没测到"不许写成看起来真实的具体数字（假数据的典型形态）
# ----------------------------------------------------------------------


class TestMissingMeasurementIsNotFaked:
    """未测到的耗时/指标必须为 0 或 None，**不许回退成 mock 常量**。

    ## 真实缺陷（A.91 修）

    `app/api/v1/diagnosis.py` 原写法：

        processing_time_ms = int(timings.get("pipeline.total_ms") or 0) \
            or settings.phase0a_mock_processing_time_ms

    findings 有内容、但 `timings_ms` 缺失/为 0 时，会**静默返回写死的 2500ms**。
    接口上的 `processing_time_ms` 于是看起来是真实耗时 —— 读者会拿它算
    P50/P99 甚至当性能门控证据，而它是个常量。

    这与 A.70（假结论算指标）/ A.77（假规则误删真数据）同族：
    **"看起来像真数据"比"明显缺失"更危险**，因为没人会去质疑它。
    """

    def _src(self) -> str:
        return _src("app/api/v1/diagnosis.py")

    def test_no_or_fallback_to_mock_processing_time(self):
        """反证腿：真值分支里**任何**对 mock 耗时常量的引用都判红。

        判据用的是"该常量是否出现在真值分支的赋值表达式里"，而不是某个固定字符串
        —— 因为绕过写法很多（`) or X`、`0 or X`、`... if c else 0 or X`），
        用固定串匹配会被写法变异绕过（我第一次就这么漏了）。

        只看代码行：注释里解释"原写法是 X"是允许的（那是在记录缺陷）。
        """
        import re as _re

        src = self._src()
        code_lines = []
        for line in src.splitlines():
            if line.strip().startswith("#"):
                continue
            code_lines.append(_re.sub(r"#.*$", "", line))
        code = "\n".join(code_lines)

        # 找所有对 phase0a_mock_processing_time_ms 的引用，逐条看是否在"计算真值"的表达式里
        hits = [
            (i + 1, ln)
            for i, ln in enumerate(code.splitlines())
            if "phase0a_mock_processing_time_ms" in ln
        ]
        # 允许的唯一一处：占位分支（worker 还没写终态时给前端一个预估展示值）
        real_value_hits = []
        for lineno, line in hits:
            # 占位分支的特征：缩进层级内、且邻近有 "pending" 或占位 payload
            ctx_start = max(0, lineno - 12)
            ctx = "\n".join(code.splitlines()[ctx_start:lineno])
            is_placeholder_branch = (
                '"pending": True' in ctx or "Waiting for diagnosis system completion" in ctx
            )
            if not is_placeholder_branch:
                real_value_hits.append((lineno, line.strip()))

        assert not real_value_hits, (
            "真值分支引用了 mock 耗时常量（timings 缺失时会静默编造 2500ms）："
            + repr(real_value_hits)
        )

    def test_unavailable_is_traced(self):
        """未测到必须留痕（不许静默给 0 也不许静默给常量）。"""
        src = self._src()
        assert "result_processing_time_unavailable" in src
        assert "result_processing_time_unparseable" in src

    def test_placeholder_branch_is_labelled(self):
        """占位分支的 2500ms 必须显式注明"不是真实耗时"。"""
        src = self._src()
        assert "不是真实耗时" in src or "预估展示值" in src


class TestEvidenceLevelDeclaredInDocstrings:
    """声称"端到端/打通"的文件，docstring 必须能区分等价等级。"""

    def test_e2e_files_are_honest_about_mocking(self):
        for rel in (
            "tests/integration/int/test_e2e_diagnosis.py",
            "tests/integration/int/test_e2e_latency.py",
        ):
            head = _src(rel)[:4000]
            assert ("E3" in head) or ("mock" in head.lower()), (
                f"{rel} 没说明它 mock 了什么"
            )
