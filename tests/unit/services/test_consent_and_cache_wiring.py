"""守卫：结果缓存的写入形状、以及同意校验用的患者身份来源。

两条都来自 2026-09-29 真机"第 1 例真实诊断"暴露的阻断级缺陷。

## 缺陷 A：结果缓存被写成 Python repr，读侧必炸

管线在写库旁顺手把结果缓存成 `str(findings)`，而读侧是
`DiagnosisResult.model_validate(json.loads(raw))`（`api/v1/diagnosis.py:501`）。
于是每次 `GET /api/v1/diagnosis/result/{id}` 都抛
`Expecting property name enclosed in double quotes: line 1 column 2 (char 1)`
—— 字符串以 `{` 开头、紧跟 `'`，正是 Python repr 而非 JSON 的特征。
而且形状也不对：写的是 `findings`，读的是**完整结果**。

修法：**只保留一个写者**（GET 处理器按完整结果形状写），管线不再碰这个键。

## 缺陷 B：患者身份恒为 "unknown"

`diagnosis_results` 表**没有 patient_id 列**（患者属于会话），而管线用
`getattr(request, "patient_id", "unknown")` 取 → 恒 `"unknown"` →
SG 查不到同意记录 → `pipeline_consent_status_unavailable` → 保守 fail-closed，
诊断必 `failed`。修法：按 `session_id` 从 `sessions` 表解析，解析不出就显式 fail-closed
（不拿 `"unknown"` 去查、也不写进 SG 审计）。
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

from app.models.schemas import (
    ConsentStatus,
    DesensitizationStatus,
    DiagnosisQualityControl,
    DiagnosisResult,
    FusionStatus,
)
from app.services.diagnosis_pipeline import _resolve_patient_id, _safe_safety_check

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
PIPELINE_SRC = (REPO_ROOT / "app" / "services" / "diagnosis_pipeline.py").read_text(
    encoding="utf-8"
)
ENDPOINT_SRC = (REPO_ROOT / "app" / "api" / "v1" / "diagnosis.py").read_text(
    encoding="utf-8"
)


class _Result:
    def __init__(self, value) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _DB:
    def __init__(self, value) -> None:
        self._value = value

    async def execute(self, *_args, **_kwargs):
        return _Result(self._value)


class _BrokenDB:
    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("db down")


class TestResultCacheHasSingleWriter:
    def test_管线不再写结果缓存(self):
        assert "RedisKey.diagnosis_result" not in PIPELINE_SRC, (
            "管线不得再写结果缓存键：它写的是 findings 的 repr，而读侧要完整结果的 JSON"
        )

    def test_端点写入的是完整结果的JSON(self):
        assert "json.dumps(diagnosis_result.model_dump())" in ENDPOINT_SRC

    def test_写入形状能被读侧解析回去(self):
        """读侧契约：`DiagnosisResult.model_validate(json.loads(raw))`。"""
        sample = DiagnosisResult(
            request_id="req_test_0001",
            status="completed",
            desensitization_status=DesensitizationStatus.FULL,
            consent_status_at_output=ConsentStatus.GRANTED,
            fusion_status=FusionStatus.NORMAL,
            diagnosis={"organ": "thyroid"},
            quality_control=DiagnosisQualityControl(),
            processing_time_ms=1,
        )

        restored = DiagnosisResult.model_validate(
            json.loads(json.dumps(sample.model_dump()))
        )

        assert restored.request_id == "req_test_0001"
        assert restored.status == "completed"

    def test_旧的repr写法确实不是JSON(self):
        """反例（钉住"这条守卫在防什么"）：repr 进 json.loads 必炸。"""
        with pytest.raises(json.JSONDecodeError):
            json.loads(str({"status": "processing"}))


def _getattr_patient_id_calls(source: str) -> int:
    """统计**真实代码**里 `getattr(x, "patient_id", ...)` 的调用数（注释/文档串不算）。"""
    count = 0
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "patient_id"
        ):
            count += 1
    return count


class TestPatientIdComesFromSession:
    def test_管线按会话解析患者(self):
        assert "_resolve_patient_id(" in PIPELINE_SRC
        assert _getattr_patient_id_calls(PIPELINE_SRC) == 0, (
            "旧写法在结果行上取 patient_id，而该表没有这一列 → 恒 unknown"
        )
        assert "patient_id=patient_id" in PIPELINE_SRC, "解析出的患者 ID 必须传给安全网关"

    @pytest.mark.asyncio
    async def test_解析成功(self):
        assert await _resolve_patient_id(_DB("patient-001"), "session-1") == "patient-001"

    @pytest.mark.asyncio
    async def test_无session_id返回None(self):
        assert await _resolve_patient_id(_DB("patient-001"), None) is None
        assert await _resolve_patient_id(_DB("patient-001"), "") is None

    @pytest.mark.asyncio
    async def test_查询异常返回None而不抛出(self):
        assert await _resolve_patient_id(_BrokenDB(), "session-1") is None

    @pytest.mark.asyncio
    async def test_会话不存在返回None(self):
        assert await _resolve_patient_id(_DB(None), "session-1") is None


class TestSafetyCheckFailsClosedWithoutPatient:
    @pytest.mark.asyncio
    async def test_患者未解析出时直接fail_closed(self):
        """不能拿 "unknown" 去查同意记录：既必然失败，又会污染 SG 审计。"""
        sg_result, sg_ok = await _safe_safety_check(
            request=type("_R", (), {"organ": "thyroid", "session_id": "s-1"})(),
            fusion_result=None,
            request_id="req_test_0001",
            patient_id=None,
        )

        assert sg_result is None
        assert sg_ok is False
