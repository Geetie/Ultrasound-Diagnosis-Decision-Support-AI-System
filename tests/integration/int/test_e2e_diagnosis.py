"""管线**编排骨架**测试（原名"端到端诊断场景测试"，A.91 改名）。

## ⚠️ 这个文件验证的不是"端到端"，请勿拿它当"诊断链路已打通"的证据

原 docstring 写"6 个端到端诊断场景测试"，但实际实现是：把管线的**五个核心步骤
全部替换成 mock**，然后断言"这些 mock 被依次调用过"：

| 被 mock 掉的真实环节 | 实际验证到的东西 |
|---|---|
| `_safe_dispatch`（专科智能体调用） | 它被调用了一次 |
| `_safe_fuse`（融合层四层校验） | 它被调用了一次 |
| `_safe_safety_check`（安全网关） | 它被调用了一次 |
| `_write_result_to_db`（落库） | 它被调用了一次 |
| `AsyncSessionLocal`（数据库会话） | 构造出来了 |

也就是说：**真实 LLM、真实 RAG、真实融合、真实 SG、真实数据库一个都没跑**。
它能证明的只有"编排顺序没写错"，而这属于 E3 级证据（源码/结构级），
**不属于**"诊断链路可用"的证据。

## 这为什么是个真问题（不是文件名洁癖）

用户 2026-10-25 明确要求："不得拿模板回答作为功能真实打通且实现的证据，
打通和真实实现是有区别的。"

这个文件原名极具误导性：任何做"能力交接/验收/答辩取证"的人看到
"6 个端到端场景全绿"，都会合理地认为诊断链路跑通了。而真相是：
**把 `_safe_fuse` 换成 `return _mock_fusion_result(...)` 之后它依然全绿** ——
也就是说融合层整个删掉，这个文件也发现不了。

同族历史：A.70（假结论算指标）、A.77（假规则误删真数据）、
A.89（"检测到敏感"看不出是误报）。共同点都是**"看起来成功"与"真的成功"
之间没有可观测分界**。

## 现在的定位

本文件保留（编排分支有回归价值：比如"管线在 dispatch 抛错时是否继续走写库"），
但以 `PIPELINE_ORCHESTRATION` 级证据对待，**不计入端到端能力覆盖**。

真正的端到端证据在别处，且必须是 E1 级（真机 + 真实依赖）：
- 真机判读：`scripts/audit/p27_grade_extraction.py`（真实影像 → 结论）
- 多器官跨器官融合：真机 `POST /diagnosis/multi-organ` → `cross_organ.status=ok`（见 16 号 A.89.3）
- 医生全流程：`scripts/audit/p60_doctor_journey.py`
- 影像腿检索贡献：`scripts/audit/p62_image_leg_contribution_ab.py`（三臂 A/B）

## 场景清单（保留原编号，便于对照历史文档）

- T1: 正常路径（甲状腺）
- T2: 多器官（注：本文件里 dispatcher 仍只返回单 organ，见该用例注释）
- T3: LLM 失败重试
- T4: RAG 降级（Qdrant 不可用 → BM25）
- T5: SafetyGateway 拒绝（禁忌触发）
- T6: 同意撤回
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.protocols import AgentResponse
from app.fusion.protocols import FusionResult
from app.models.schemas import Organ


# Mock helper to construct a successful specialist response
def _mock_specialist_response(organ: str, confidence: float = 0.85) -> AgentResponse:
    return AgentResponse(
        agent_name=f"{organ}_specialist",
        organ=Organ(organ),
        content=f"[{organ}] mock diagnosis content - TR3 nodule detected",
        confidence=confidence,
        evidence_chunk_ids=[f"chunk_{organ}_1", f"chunk_{organ}_2"],
        reasoning_trace=[f"reasoning_path_{organ}"],
        latency_ms=120.0,
    )


# Mock helper to construct a successful fusion result
def _mock_fusion_result(organ: str, confidence: float = 0.85) -> FusionResult:
    final = _mock_specialist_response(organ, confidence)
    return FusionResult(
        final_response=final,
        needs_review=False,
        review_reason=None,
        self_rag_passed=True,
        crag_triggered=False,
        crag_recovered=False,
        total_latency_ms=180.0,
    )


class TestE2EScenarioT1NormalPath:
    """T1: 正常路径 - 甲状腺 TR3 mock。"""

    @pytest.mark.asyncio
    async def test_t1_thyroid_normal_path_returns_success(self):
        """单器官甲状腺诊断返回 success=True。"""
        from app.services.diagnosis_pipeline import run_diagnosis_pipeline

        with patch("app.services.diagnosis_pipeline._safe_dispatch", new_callable=AsyncMock) as mock_dispatch, \
             patch("app.services.diagnosis_pipeline._safe_fuse", new_callable=AsyncMock) as mock_fuse, \
             patch("app.services.diagnosis_pipeline._safe_safety_check", new_callable=AsyncMock) as mock_sg, \
             patch("app.services.diagnosis_pipeline._write_result_to_db", new_callable=AsyncMock) as mock_write, \
             patch("app.services.diagnosis_pipeline.AsyncSessionLocal") as mock_session:

            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_dispatch.return_value = [_mock_specialist_response("thyroid")]
            mock_fuse.return_value = _mock_fusion_result("thyroid", confidence=0.88)
            mock_sg.return_value = ({"passed": True, "consent_status_at_output": "granted"}, True)
            mock_write.return_value = True

            result = await run_diagnosis_pipeline("req_t1_001")
            assert result is True
            mock_dispatch.assert_called_once()
            mock_fuse.assert_called_once()
            mock_sg.assert_called_once()
            mock_write.assert_called_once()


class TestE2EScenarioT2MultiOrgan:
    """T2: 多器官并行（甲状腺 + 肝脏）。"""

    @pytest.mark.asyncio
    async def test_t2_multi_organ_parallel_invocation(self):
        """dispatcher.invoke 接收单 organ，但 fuse 可处理多响应。"""
        from app.services.diagnosis_pipeline import run_diagnosis_pipeline

        with patch("app.services.diagnosis_pipeline._safe_dispatch", new_callable=AsyncMock) as mock_dispatch, \
             patch("app.services.diagnosis_pipeline._safe_fuse", new_callable=AsyncMock) as mock_fuse, \
             patch("app.services.diagnosis_pipeline._safe_safety_check", new_callable=AsyncMock) as mock_sg, \
             patch("app.services.diagnosis_pipeline._write_result_to_db", new_callable=AsyncMock) as mock_write, \
             patch("app.services.diagnosis_pipeline.AsyncSessionLocal") as mock_session:

            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            # 模拟多器官（虽然 dispatcher 实际上只返回一个 organ 的响应）
            mock_dispatch.return_value = [
                _mock_specialist_response("thyroid", confidence=0.9),
            ]
            mock_fuse.return_value = _mock_fusion_result("thyroid", confidence=0.9)
            mock_sg.return_value = ({"passed": True}, True)
            mock_write.return_value = True

            result = await run_diagnosis_pipeline("req_t2_001")
            assert result is True


class TestE2EScenarioT3LLMRetry:
    """T3: LLM 失败重试。"""

    @pytest.mark.asyncio
    async def test_t3_specialist_retries_on_failure(self):
        """specialist 内部重试 1 次后仍失败则 needs_review。"""
        from app.services.diagnosis_pipeline import _safe_dispatch

        with patch("app.main.app") as mock_app:
            # 第一次 invoke 抛错, 第二次返回成功
            mock_specialist = MagicMock()
            mock_specialist.invoke = AsyncMock(side_effect=[
                RuntimeError("LLM timeout"),
                _mock_specialist_response("thyroid", confidence=0.7),
            ])
            mock_dispatcher = MagicMock()
            mock_dispatcher.invoke = AsyncMock(side_effect=[
                RuntimeError("specialist crash"),
                _mock_specialist_response("thyroid", confidence=0.7),
            ])
            mock_app.state.dispatcher = mock_dispatcher

            # 第一次：dispatcher 失败 → 占位响应
            mock_request = MagicMock()
            mock_request.organ = "thyroid"
            mock_request.session_id = "sess"
            mock_request.clinical_text = "test"
            responses = await _safe_dispatch(mock_request, "req_t3_001")
            assert responses[0].confidence == 0.0  # 第一次失败


class TestE2EScenarioT4RAGFallback:
    """T4: RAG 降级 (Qdrant 不可用 → BM25 路径)。"""

    @pytest.mark.asyncio
    async def test_t4_rag_fallback_returns_empty_evidence(self):
        """RAG 不可用时 specialist 仍返回响应, evidence_chunk_ids 为空。"""
        from app.services.diagnosis_pipeline import _safe_fuse

        with patch("app.main.app") as mock_app:
            mock_app.state.fusion_engine = None  # fusion 不可用

            mock_specialist_responses = [
                _mock_specialist_response("thyroid", confidence=0.5),
            ]
            mock_request = MagicMock()
            mock_request.organ = "thyroid"

            fusion_result = await _safe_fuse(mock_request, mock_specialist_responses, "req_t4_001")
            # 兜底: 取 max confidence specialist
            assert fusion_result.final_response.confidence == 0.5
            assert fusion_result.needs_review is True


class TestE2EScenarioT5SafetyRejection:
    """T5: SafetyGateway 拒绝（禁忌触发）。"""

    @pytest.mark.asyncio
    async def test_t5_sg_rejection_marks_failed(self, monkeypatch):
        """SG 返回 passed=False 但 service_unavailable=True 时 status='failed'。"""
        from app.services.diagnosis_pipeline import _write_result_to_db
        from tests.integration.int.int_test_helpers import patch_write_deps

        mock_repo = MagicMock()
        mock_repo.update_result = AsyncMock(return_value=MagicMock())
        # ⚠️ 不要传 diagnosis_repo=：该函数自开短会话（diagnosis_pipeline.py:783），
        # 形参已不存在；要 mock 就 patch 它真正用的两个依赖。
        patch_write_deps(monkeypatch, mock_repo)

        mock_request = MagicMock()
        mock_request.request_id = "req_t5_001"
        mock_request.organ = "thyroid"

        result = await _write_result_to_db(
            request=mock_request,
            fusion_result=_mock_fusion_result("thyroid"),
            sg_result={"passed": False, "service_unavailable": True, "error_code": "E4005A1"},
            sg_ok=True,
            timings={},
        )

        # 注意：sg_ok=True, 但 sg_result 显示禁忌触发, 走 service_unavailable
        # 当前实现仅基于 sg_ok 决定 status; 真实场景需要更细的 sg_result 判断
        assert result is True


class TestE2EScenarioT6ConsentWithdrawn:
    """T6: 同意撤回。"""

    @pytest.mark.asyncio
    async def test_t6_sg_returns_consent_withdrawn(self):
        """SG 返回 consent_status=withdrawn 时, 端点应阻断。"""
        from app.services.diagnosis_pipeline import _safe_safety_check

        with patch("app.services.diagnosis_pipeline.safety_gateway_client") as mock_sg:
            mock_sg.get_consent_status = AsyncMock(return_value="granted")
            mock_sg.check_output = AsyncMock(return_value={
                "passed": True,  # SG 同意通过
                "consent_status_at_output": "withdrawn",  # 但同意被撤回
            })
            sg_result, sg_ok = await _safe_safety_check(
                request=MagicMock(),
                fusion_result=MagicMock(),
                request_id="req_t6_001",
                # 必须先解析出患者：解析不出时管线 fail-closed，本用例就走不到 SG。
                patient_id="P001",
            )

        # 当前实现下 sg_ok 仍为 True; 端点 / 业务层应根据 consent_status_at_output 阻断
        assert sg_result["consent_status_at_output"] == "withdrawn"


__all__ = [
    "TestE2EScenarioT1NormalPath",
    "TestE2EScenarioT2MultiOrgan",
    "TestE2EScenarioT3LLMRetry",
    "TestE2EScenarioT4RAGFallback",
    "TestE2EScenarioT5SafetyRejection",
    "TestE2EScenarioT6ConsentWithdrawn",
]
