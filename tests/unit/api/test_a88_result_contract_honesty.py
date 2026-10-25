"""A.88 A3 守卫：结果接口的三个字段必须是**推导值**，不是硬编码常量。

## 缺陷（契约在说谎）

``app/api/v1/diagnosis.py`` 的主读路径曾写死：

    consistency_flag=ConsistencyFlag.CONSISTENT   # 恒"一致"
    fusion_status=FusionStatus.NORMAL             # 恒"正常"
    hallucination_severity=None                    # 无生产者（这条是诚实的）

而一致性校验在生产上**一次都没跑过**：既往诊断从未接线（A2）、标准档的指南
对比因量纲错配拿不到任何 chunk（A1）。契约却始终宣布"一致"——医生据此跳过复核。
cancelled 分支同样写死 CONSISTENT/NORMAL，而那条路径连融合层都没跑。

## 修法（本文件逐条钉住）

* ``fusion_status``：有 ``findings.fusion_result`` → NORMAL，否则 **BYPASSED**；
* ``consistency_flag``：``compared_with_prior or compared_with_guidelines`` 为真
  才给 CONSISTENT / INCONSISTENT（按 ``consistency_severity == "critical"``）；
  **没评估过 → None**（schema 里该字段是 ``ConsistencyFlag | None``）；
* ``hallucination_severity``：**T5 接线后有生产者**（管线在 FULL 档跑幻觉检测，
  写 ``hallucination_severity`` + ``hallucination_checked``）。读侧口径：
  ``hallucination_checked`` 不是 True → None（未评估，不编造）；已评估且取值可识别
  → 归一为枚举；不可识别 → None（不猜）。详见
  :class:`TestHallucinationSeverityStaysHonest`。

反证腿：把任一处改回硬编码常量，本文件的用例即红。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Response

from app.api.v1 import diagnosis as diagnosis_api
from app.api.v1.diagnosis import get_diagnosis_result
from app.models.schemas import ConsistencyFlag, FusionStatus, HallucinationSeverity

pytestmark = pytest.mark.track_api

DIAGNOSIS_PY = Path(diagnosis_api.__file__)


def _row(
    *,
    status: str = "completed",
    fusion_result: dict | None = None,
    findings: dict | None = None,
) -> Mock:
    row = Mock()
    row.request_id = "req_1"
    row.status = status
    row.session_id = "sess_1"
    row.organ = "thyroid"
    row.created_at = datetime.now(timezone.utc)
    row.confidence = 0.8
    row.knowledge_ids = None
    row.result_pushed = True  # 避免触发 _push_diagnosis_result
    row.patient_context = None
    if findings is not None:
        row.findings = findings
    elif fusion_result is not None:
        row.findings = {"organ": "thyroid", "fusion_result": fusion_result}
    else:
        row.findings = {}
    return row


def _stub_io(monkeypatch: pytest.MonkeyPatch, row: Mock) -> None:
    """把端点依赖（Redis / SG / 仓储）全部打桩，只留被测的推导逻辑。"""

    class _FakeResult:
        error = None
        value = None

        def decode_value(self):  # pragma: no cover - 不应被调用
            raise AssertionError("测试不应命中缓存")

    class _FakePool:
        @staticmethod
        async def get_with_error(_key: str) -> _FakeResult:
            return _FakeResult()

        @staticmethod
        async def setex(*_args, **_kwargs) -> None:
            return None

    monkeypatch.setattr(diagnosis_api, "RedisPool", _FakePool)

    class _FakeSG:
        async def check_output(self, **_kwargs):
            return {"passed": True, "consent_status_at_output": "granted"}

    monkeypatch.setattr(diagnosis_api, "safety_gateway_client", _FakeSG())

    repo = Mock()
    repo.get_result = AsyncMock(return_value=row)
    session_repo = Mock()
    session_repo.get_session = AsyncMock(return_value=SimpleNamespace(patient_id="pat_1"))
    consent_repo = Mock()
    consent_repo.get_latest_consent = AsyncMock(
        return_value=SimpleNamespace(status="granted")
    )

    monkeypatch.setattr(diagnosis_api, "check_consent_status", lambda *_a, **_k: SimpleNamespace(value="granted"))
    return repo, session_repo, consent_repo


async def _call(monkeypatch: pytest.MonkeyPatch, row: Mock):
    repo, session_repo, consent_repo = _stub_io(monkeypatch, row)
    return await get_diagnosis_result(
        request_id="req_1",
        response=Response(),
        db=Mock(),
        diagnosis_repo=repo,
        consent_repo=consent_repo,
        session_repo=session_repo,
        current_user={"user_id": "u1"},
    )


class TestFusionStatusDerivation:
    """有融合结果 → NORMAL；没有 → BYPASSED（不再恒 NORMAL）。"""

    async def test_with_fusion_result_is_normal(self, monkeypatch):
        row = _row(fusion_result={"needs_review": False, "self_rag_passed": True})
        result = await _call(monkeypatch, row)
        assert result.fusion_status == FusionStatus.NORMAL

    async def test_without_fusion_result_is_bypassed(self, monkeypatch):
        """反证腿：修复前这里恒 NORMAL（融合层压根没跑也宣称"正常"）。"""
        row = _row(findings={})
        result = await _call(monkeypatch, row)
        assert result.fusion_status == FusionStatus.BYPASSED, (
            "没有 findings.fusion_result 却声明 fusion_status=normal —— 契约在说谎"
        )

    async def test_placeholder_payload_is_bypassed(self, monkeypatch):
        """processing 占位 payload（worker 未写入）同样没有融合结果 → BYPASSED。"""
        row = _row(status="processing", findings=None)
        result = await _call(monkeypatch, row)
        assert result.fusion_status == FusionStatus.BYPASSED


class TestConsistencyFlagDerivation:
    """没评估过 → None；只有真比过才给 consistent/inconsistent。"""

    async def test_never_compared_yields_none(self, monkeypatch):
        """反证腿：修复前恒 CONSISTENT —— 一次都没比过却宣称"一致"。"""
        row = _row(
            fusion_result={
                "needs_review": False,
                "compared_with_prior": False,
                "compared_with_guidelines": False,
                "consistency_severity": "info",
            }
        )
        result = await _call(monkeypatch, row)
        assert result.consistency_flag is None, (
            "两路都没比过却声明 consistency_flag=consistent —— 这正是 A.88 A3 的缺陷"
        )

    async def test_missing_compared_flags_yields_none(self, monkeypatch):
        """融合结果里没有这两个键（旧落库行）→ 视为未评估，不猜。"""
        row = _row(fusion_result={"needs_review": False})
        result = await _call(monkeypatch, row)
        assert result.consistency_flag is None

    async def test_no_fusion_result_yields_none(self, monkeypatch):
        row = _row(findings={})
        result = await _call(monkeypatch, row)
        assert result.consistency_flag is None

    async def test_compared_with_prior_and_info_is_consistent(self, monkeypatch):
        row = _row(
            fusion_result={
                "compared_with_prior": True,
                "compared_with_guidelines": False,
                "consistency_severity": "info",
            }
        )
        result = await _call(monkeypatch, row)
        assert result.consistency_flag == ConsistencyFlag.CONSISTENT

    async def test_compared_with_guidelines_only_also_counts(self, monkeypatch):
        row = _row(
            fusion_result={
                "compared_with_prior": False,
                "compared_with_guidelines": True,
                "consistency_severity": "warning",
            }
        )
        result = await _call(monkeypatch, row)
        assert result.consistency_flag == ConsistencyFlag.CONSISTENT

    async def test_critical_severity_is_inconsistent(self, monkeypatch):
        row = _row(
            fusion_result={
                "compared_with_prior": True,
                "consistency_severity": "critical",
            }
        )
        result = await _call(monkeypatch, row)
        assert result.consistency_flag == ConsistencyFlag.INCONSISTENT


class TestHallucinationSeverityStaysHonest:
    """A.88 后续（T5 接线后）：`hallucination_severity` **有生产者**了。

    历史：本类原先断言"无生产者，不得填值"，用来防止后人顺手填一个枚举。
    T5（inference-wiring）把幻觉检测接进管线并把结果写进
    ``findings.fusion_result``（``hallucination_severity`` + ``hallucination_checked``），
    所以**那条断言的前提已经变了** —— 留着它就会把"已有真实生产者"钉成
    "不许有"，反过来阻碍正确实现。

    现在改为钉**新的诚实口径**（更强的一条）：
      * ``hallucination_checked`` 不为 True → None（未评估，不编造）；
      * 已评估 + 可识别取值 → 归一为枚举；
      * 已评估 + 不可识别取值 → None（不猜，且留痕）。
    """

    async def test_unchecked_is_none(self, monkeypatch):
        """未评估（没有 checked 标记）→ None，不得凭空填值。"""
        row = _row(
            fusion_result={
                "compared_with_prior": True,
                "consistency_severity": "info",
                # 故意给一个 severity 但**没有** checked 标记：来源不可信
                "hallucination_severity": "severe",
            }
        )
        result = await _call(monkeypatch, row)
        assert result.hallucination_severity is None

    async def test_checked_value_is_read(self, monkeypatch):
        """已评估且给出可识别取值 → 必须真的读出来（证明接线有效）。"""
        row = _row(
            fusion_result={
                "compared_with_prior": True,
                "consistency_severity": "info",
                "hallucination_checked": True,
                "hallucination_severity": "moderate",
            }
        )
        result = await _call(monkeypatch, row)
        assert result.hallucination_severity == HallucinationSeverity.MODERATE

    async def test_checked_but_clean_is_none(self, monkeypatch):
        """已评估且干净 → None（契约里没有 "clean" 取值，None = 未检出）。"""
        row = _row(
            fusion_result={
                "compared_with_prior": True,
                "consistency_severity": "info",
                "hallucination_checked": True,
                "hallucination_severity": None,
            }
        )
        result = await _call(monkeypatch, row)
        assert result.hallucination_severity is None

    async def test_unrecognized_value_is_not_guessed(self, monkeypatch):
        """取值不可识别 → None（猜成 mild 等于凭空减轻一条质检结论）。"""
        row = _row(
            fusion_result={
                "compared_with_prior": True,
                "consistency_severity": "info",
                "hallucination_checked": True,
                "hallucination_severity": "totally-unknown",
            }
        )
        result = await _call(monkeypatch, row)
        assert result.hallucination_severity is None

    def test_source_keeps_honesty_discipline(self):
        """源码守卫：必须仍以 `hallucination_checked` 为判据（防止后人改成直接透传）。"""
        src = DIAGNOSIS_PY.read_text(encoding="utf-8")
        assert "hallucination_checked" in src
        assert "未评估" in src


class TestCancelledBranchIsHonest:
    """cancelled 分支：连融合层都没跑，不得声明 consistent/normal。"""

    def _cancelled_row(self) -> Mock:
        row = Mock()
        row.request_id = "req_cancelled"
        row.status = "cancelled"
        row.session_id = "sess_x"
        row.organ = "thyroid"
        row.findings = None
        row.created_at = datetime.now(timezone.utc)
        row.confidence = None
        row.knowledge_ids = None
        row.result_pushed = False
        row.patient_context = None
        return row

    async def test_cancelled_flags_are_not_asserted(self, monkeypatch):
        _stub_io(monkeypatch, self._cancelled_row())
        session_repo = Mock()
        session_repo.get_session = AsyncMock(
            side_effect=AssertionError("cancelled 不应触达会话查询")
        )
        consent_repo = Mock()
        consent_repo.get_latest_consent = AsyncMock(
            side_effect=AssertionError("cancelled 不应触达同意门")
        )

        result = await get_diagnosis_result(
            request_id="req_cancelled",
            response=Response(),
            db=Mock(),
            diagnosis_repo=Mock(get_result=AsyncMock(return_value=self._cancelled_row())),
            consent_repo=consent_repo,
            session_repo=session_repo,
            current_user={"user_id": "u1"},
        )

        assert result.status == "cancelled"
        assert result.consistency_flag is None, (
            "cancelled 分支（一致性校验从未执行）仍声明 consistent"
        )
        assert result.fusion_status == FusionStatus.BYPASSED, (
            "cancelled 分支（融合层从未产出）仍声明 normal"
        )
        # 不携带临床内容的不变量保持不变（既有测试也钉这条）
        assert result.diagnosis.get("pending") is True


class TestNoHardcodedConstantsRemain:
    """源码级反证腿：三个常量写法不得回来。"""

    @pytest.fixture
    def src(self) -> str:
        return DIAGNOSIS_PY.read_text(encoding="utf-8")

    def test_no_hardcoded_consistent(self, src):
        assert "consistency_flag=ConsistencyFlag.CONSISTENT" not in src

    def test_no_hardcoded_normal(self, src):
        assert "fusion_status=FusionStatus.NORMAL" not in src

    def test_derivation_present(self, src):
        assert "fusion_status = FusionStatus.NORMAL if has_fusion_result else FusionStatus.BYPASSED" in src
        assert "consistency_flag = None" in src
