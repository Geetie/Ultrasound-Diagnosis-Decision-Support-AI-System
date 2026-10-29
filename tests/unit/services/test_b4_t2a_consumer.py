"""02-1 S2/S3：t2a_classification 的接口消费者（类型化出口）。

背景（02-1②）：findings["t2a_classification"] 此前只落 DB JSONB，
app/api/v1/diagnosis.py 组装处零派生字段、app/models/schemas.py 零声明、
前端零渲染 —— "打开开关 ≠ 医生看得见"（A.95.7 教训的最纯粹形态）。

判据（02-1⑤）：
1. DiagnosisResult 契约含可选 t2a_classification（默认 None，
   不破坏既有构造点 —— 与 nodule_segmentation 同风格）；
2. 结果接口组装处存在 t2a_classification 派生字段（源码级断言，
   与 tests/unit/services/test_a88_t2a_bypass.py:251 同风格）；
3. 缺失时回 {"available": False, "reason": "not_run"}（字段不消失）；
4. 红线（02-1②/⑥）：组装逻辑只透传 findings，不影响 status ——
   本文件断言"组装处 t2a_classification 不做任何真值判断"。
"""
from __future__ import annotations

from pathlib import Path

from app.models.schemas import DiagnosisResult

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DIAGNOSIS_API = _REPO_ROOT / "app" / "api" / "v1" / "diagnosis.py"
_SCHEMAS = _REPO_ROOT / "app" / "models" / "schemas.py"


class TestT2aSchemaExit:
    """02-1⑤ 判据③：契约里有类型化出口。"""

    def test_schema_has_optional_t2a_field(self):
        fields = DiagnosisResult.model_fields
        assert "t2a_classification" in fields, (
            "DiagnosisResult 缺 t2a_classification —— T2a 产出无消费者（02-1②）"
        )
        field = fields["t2a_classification"]
        assert not field.is_required(), "必须带默认值（不破坏既有构造点）"
        assert field.default is None

    def test_schema_comment_keeps_auxiliary_redline(self):
        src = _SCHEMAS.read_text(encoding="utf-8")
        assert "辅助参考" in src
        assert "不参与 status / confidence / review_reason" in src


class TestT2aApiAssembly:
    """02-1⑤ 反证腿 R1/R3：接口侧必须有键、缺失必须有 not_run 兜底。"""

    def test_api_assembly_derives_t2a_from_findings(self):
        src = _DIAGNOSIS_API.read_text(encoding="utf-8")
        assert '(diagnosis_payload or {}).get("t2a_classification")' in src, (
            "结果接口组装处缺 t2a_classification 派生字段 —— "
            "落库有值但医生看不见（R3 反证腿）"
        )

    def test_api_assembly_has_not_run_fallback(self):
        src = _DIAGNOSIS_API.read_text(encoding="utf-8")
        # 与 nodule_segmentation 分支同构的缺失兜底（"未做"≠字段消失）
        assert '{"available": False, "reason": "not_run"}' in src

    def test_t2a_never_gates_status(self):
        """红线：组装处不得用 t2a_classification 参与 status/review 判定。"""
        src = _DIAGNOSIS_API.read_text(encoding="utf-8")
        for banned in (
            "if t2a_classification",          # 不得做条件分支
            "status = t2a",                   # 不得改 status
            "confidence = t2a",               # 不得改 confidence
        ):
            assert banned not in src, f"出现红线模式: {banned}"
