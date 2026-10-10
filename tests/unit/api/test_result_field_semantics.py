"""结果字段语义守卫：三处占位硬编码必须取真实值。

## 为什么有这条测试

2026-09-28 接线审计发现三处**硬编码占位**，它们的共同危害不是"少了个功能"，
而是**对外表达了错误的事实**：

| 位置 | 原写法 | 危害 |
|---|---|---|
| `api/v1/diagnosis.py` `quality_control.self_rag_passed` | 硬编码 `False` | "未评估"被表达成"未通过" |
| `api/v1/diagnosis.py` `quality_control.consistency_check_passed` | 硬编码 `True` | "没检查过"被表达成"检查通过" |
| `api/v1/diagnosis.py` `ai_diagnosis_failed` | 恒 `False` | MonitorAgent 的 R3（同器官连续失败）**永远不会触发** |
| `services/diagnosis_pipeline.py` `consent_status_at_input` | 硬编码 `"granted"` | 输入侧同意校验形同虚设；审计链记录的状态是伪造的；SG 的 fail-closed 分支永不触发 |

`ai_diagnosis_failed` 恒 False 还会**级联**：`MonitorAgent._is_failure`
（`agents/monitor.py:138-142`）同时读 status 与该字段，字段恒假 → R3 窗口永远不被写入。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import diagnosis_pipeline as pipeline_module
from app.services.diagnosis_pipeline import _safe_safety_check

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DIAGNOSIS_API = PROJECT_ROOT / "app" / "api" / "v1" / "diagnosis.py"
PIPELINE = PROJECT_ROOT / "app" / "services" / "diagnosis_pipeline.py"


def _request(patient_id: str = "P-1") -> SimpleNamespace:
    return SimpleNamespace(
        organ="thyroid",
        session_id="sess-1",
        patient_id=patient_id,
        request_id="req-1",
    )


class TestConsentStatusIsActuallyQueried:
    """`consent_status_at_input` 必须来自 SG 直查，且拿不到就 fail-closed。"""

    async def test_real_consent_status_is_passed_through(self, monkeypatch):
        client = MagicMock()
        client.get_consent_status = AsyncMock(return_value="granted")
        client.check_output = AsyncMock(return_value={"passed": True})
        monkeypatch.setattr(pipeline_module, "safety_gateway_client", client)

        # 患者身份不再从结果行取（该表没有 patient_id 列），而是由调用方按会话解析后传入。
        sg_result, ok = await _safe_safety_check(
            _request(), MagicMock(), "req-1", patient_id="P-1"
        )

        client.get_consent_status.assert_awaited_once_with("P-1")
        assert ok is True
        assert sg_result == {"passed": True}
        assert client.check_output.await_args.kwargs["consent_status_at_input"] == "granted"

    async def test_withdrawn_status_is_not_laundered_into_granted(self, monkeypatch):
        """撤回状态必须原样传给 SG —— 不能被"简化"成 granted。"""
        client = MagicMock()
        client.get_consent_status = AsyncMock(return_value="withdrawn")
        client.check_output = AsyncMock(return_value={"passed": False})
        monkeypatch.setattr(pipeline_module, "safety_gateway_client", client)

        _, ok = await _safe_safety_check(
            _request(), MagicMock(), "req-1", patient_id="P-1"
        )

        assert ok is True
        assert client.check_output.await_args.kwargs["consent_status_at_input"] == "withdrawn"

    async def test_unavailable_consent_status_fails_closed(self, monkeypatch):
        """拿不到同意状态 → 不得继续输出结论（fail-closed）。"""
        client = MagicMock()
        client.get_consent_status = AsyncMock(return_value=None)
        client.check_output = AsyncMock(return_value={"passed": True})
        monkeypatch.setattr(pipeline_module, "safety_gateway_client", client)

        sg_result, ok = await _safe_safety_check(
            _request(), MagicMock(), "req-1", patient_id="P-1"
        )

        assert (sg_result, ok) == (None, False)
        client.check_output.assert_not_awaited()


class TestNoHardcodedPlaceholdersRemain:
    """源码级断言：三个硬编码写法不得回来（它们是本批缺陷的直接对手）。"""

    @pytest.fixture(scope="class")
    def api_source(self) -> str:
        return DIAGNOSIS_API.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def pipeline_source(self) -> str:
        return PIPELINE.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def main_read_path_source(self, api_source) -> str:
        """主读路径源码（cancelled 提前返回之后的段落）。

        A.49 起结果读路径有两条构造分支：cancelled 提前返回（占位 payload，
        「未评估→不得声称通过」的保守 False 是**批准的语义**）与主路径。
        本批守卫的对象是主路径 —— 锚定同意门之后的源码，既防守卫误伤
        cancelled 分支、也防有人把硬编码塞回主路径。
        """
        anchor = "consent_status = check_consent_status(consent_record, patient_id)"
        idx = api_source.find(anchor)
        assert idx != -1, "主读路径锚点丢失（同意门被移走？）"
        return api_source[idx:]

    def test_quality_control_is_not_hardcoded(self, main_read_path_source):
        assert "self_rag_passed=False,\n" not in main_read_path_source
        assert "consistency_check_passed=True,\n" not in main_read_path_source

    def test_quality_control_derives_from_real_signals(self, api_source):
        # Self-RAG 取管线写回的真实值
        assert 'fusion_payload.get("self_rag_passed")' in api_source
        # 一致性以融合层的 review_reason 为真实信号
        assert '"consistency_failed"' in api_source

    def test_ai_diagnosis_failed_follows_status(self, api_source, main_read_path_source):
        assert "ai_diagnosis_failed=False,\n" not in main_read_path_source
        assert 'ai_diagnosis_failed = result_status in ("failed", "timeout")' in api_source

    def test_pipeline_no_longer_hardcodes_consent(self, pipeline_source):
        assert 'consent_status_at_input = "granted"' not in pipeline_source
        assert "safety_gateway_client.get_consent_status(" in pipeline_source

    def test_pipeline_payload_carries_consistency_signal(self, pipeline_source):
        """结果接口要能取到一致性真实信号，管线必须把它写进 payload。"""
        assert '"review_reason": getattr(fusion_result, "review_reason", None)' in (
            pipeline_source
        )
        assert '"needs_review": getattr(fusion_result, "needs_review", False)' in (
            pipeline_source
        )


class TestMonitorFailureCriteriaAlignment:
    """`ai_diagnosis_failed` 与 MonitorAgent 的失败判据必须同源，否则 R3 永不触发。"""

    @pytest.fixture(scope="class")
    def monitor_source(self) -> str:
        return (PROJECT_ROOT / "app" / "agents" / "monitor.py").read_text(
            encoding="utf-8"
        )

    def test_monitor_reads_both_status_and_flag(self, monitor_source):
        """monitor 的失败判据同时看 status 与 ai_diagnosis_failed（两者缺一不可）。"""
        assert '"failed"' in monitor_source and '"timeout"' in monitor_source
        assert "ai_diagnosis_failed" in monitor_source

    def test_failure_statuses_match_api_derivation(self):
        """API 用的失败状态集合必须覆盖 monitor 认的全部终态。"""
        api_source = DIAGNOSIS_API.read_text(encoding="utf-8")

        assert 'result_status in ("failed", "timeout")' in api_source
