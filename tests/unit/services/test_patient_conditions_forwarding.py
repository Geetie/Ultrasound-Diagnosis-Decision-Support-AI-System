"""守卫：请求携带的患者条件必须被**转发**到安全网关的禁忌判定。

## 为什么需要它（2026-09-29 真机第 1 例真实诊断暴露）

一次诊断永远进不了 `completed`，表面症状是
`review=检测到禁忌条件：检测到4条禁忌`。根因是一条完整的"数据丢弃链"：

    DiagnosisRequest.patient_context  →  create_request(patient_context=...) 收了**就丢**
                                        （模型里压根没有这一列）
      → 后台管线 `_safe_safety_check` 只发 organ/findings/recommendation
      → SG 的 `_match_condition` 取不到 `diagnosis_data["patient_conditions"]`
      → 落到"缺条件数据"分支，而该分支**按设计返回 True**（保守判定）
      → 每次诊断命中全部全局禁忌 → 恒 `requires_manual_review`

于是无论诊断质量如何，系统都给不出自动结论。本守卫钉住三件事：

1. `create_request` 必须把 patient_context **写进行里**（不能再收了就丢）；
2. 管线必须把 `conditions` 转发成 `diagnosis_data.patient_conditions`；
3. **未提供时不得转发**（`None` vs `[]` 的方向性差异：前者保持 SG 保守判定，
   后者表示"已确认无条件"→ 正常评估）。这条是"宁可漏转也不 fail-open"。
"""
from __future__ import annotations

import asyncio
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.diagnosis_pipeline import _safe_safety_check

PIPELINE_SRC = (
    pathlib.Path(__file__).resolve().parents[3]
    / "app"
    / "services"
    / "diagnosis_pipeline.py"
).read_text(encoding="utf-8")


class _Row:
    """诊断结果行（只带判定所需的字段）。"""

    def __init__(self, patient_context=None) -> None:
        self.organ = "thyroid"
        self.session_id = "s-1"
        self.patient_context = patient_context


def _run_safety_check(patient_context):
    """跑一次 `_safe_safety_check`，返回发给 SG 的 output_data。"""
    captured: dict = {}

    async def _fake_check_output(**kwargs):
        captured.update(kwargs)
        return {"status": "passed"}

    with (
        patch(
            "app.services.diagnosis_pipeline.safety_gateway_client.get_consent_status",
            new=AsyncMock(return_value="granted"),
        ),
        patch(
            "app.services.diagnosis_pipeline.safety_gateway_client.check_output",
            new=AsyncMock(side_effect=_fake_check_output),
        ),
        patch(
            "app.services.diagnosis_pipeline._register_l3_existing_sg_check",
            new=AsyncMock(),
        ),
    ):
        asyncio.run(
            _safe_safety_check(
                request=_Row(patient_context),
                fusion_result=MagicMock(),
                request_id="req_test_0001",
                patient_id="P-1",
            )
        )

    return captured.get("output_data", {})


class TestConditionsAreForwarded:
    def test_空列表要转发表示已确认无条件(self):
        """`[]` 是"医生已确认无条件"的明确表达，必须让它参与评估（否则永远命中禁忌）。"""
        output_data = _run_safety_check({"conditions": []})

        assert output_data.get("diagnosis_data") == {"patient_conditions": []}

    def test_有具体条件时如实转发(self):
        output_data = _run_safety_check({"conditions": ["pregnancy"]})

        assert output_data["diagnosis_data"]["patient_conditions"] == ["pregnancy"]

    def test_未提供条件时不转发以保持保守判定(self):
        """**方向性关键**：None 不能当成"无条件"，否则把 SG 的保守判定静默关掉。"""
        output_data = _run_safety_check(None)

        assert "diagnosis_data" not in output_data

    def test_显式None同样不转发(self):
        """`model_dump()` 会把未提供的可选字段输出成 None —— 不能误判为"已确认"。"""
        output_data = _run_safety_check({"conditions": None, "age": 50})

        assert "diagnosis_data" not in output_data

    def test_非字典上下文不炸(self):
        output_data = _run_safety_check("not-a-dict")

        assert "diagnosis_data" not in output_data


class TestRepositoryPersistsContext:
    def test_create_request把患者上下文落进行里(self):
        from app.models.schemas import ResponseTier
        from app.repositories.diagnosis_repository import DiagnosisRepository

        class _FakeSession:
            def __init__(self) -> None:
                self.added: list = []

            def add(self, obj) -> None:
                self.added.append(obj)

            async def flush(self) -> None:
                return None

        fake = _FakeSession()
        repo = DiagnosisRepository(fake)  # type: ignore[arg-type]

        asyncio.run(
            repo.create_request(
                session_id="s-1",
                organ="thyroid",
                response_tier=ResponseTier.FULL,
                patient_context={"age": 50, "conditions": []},
                request_id="req_test_0002",
            )
        )

        assert fake.added[0].patient_context == {"age": 50, "conditions": []}, (
            "此前这个入参收了就丢（模型无对应列），禁忌判定因此永远拿不到条件数据"
        )

    def test_源码里保留了转发逻辑(self):
        assert "diagnosis_data" in PIPELINE_SRC
        assert "patient_conditions" in PIPELINE_SRC


class TestSchemaCarriesConditions:
    def test_患者上下文有结构化条件字段(self):
        from app.models.schemas import PatientContext

        assert PatientContext(conditions=["pregnancy"]).conditions == ["pregnancy"]
        assert PatientContext().conditions is None
