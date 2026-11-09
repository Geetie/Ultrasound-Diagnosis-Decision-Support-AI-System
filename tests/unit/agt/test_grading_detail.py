"""分级完整口径 `grading_detail`（2026-11-09）。

## 要解决的问题

知识库同时装 C-TIRADS 2021 与 ACR TI-RADS 2017，两者**主级都是 "4"**：

| 体系 | 码 | 含义 |
|---|---|---|
| C-TIRADS 2021（计数法） | `4A` | **低度**可疑恶性（2%~10%） |
| ACR TI-RADS 2017（加权法） | `TR4` | **中度**可疑恶性 |

`_to_tr_canonical("4a") → "TR4"` 把**低度**和**中度**合并成同一码
→ 标题显示 `甲状腺 · TR4` 而正文说 `4A 类`，**风险被高估一档**。

修法：`AgentResponse.grading_detail` 带体系原始码，展示层用它拼标题；
**下游契约（融合层/评测/DB）仍读 `TR4` 主级，零变化。**
"""

from __future__ import annotations

import pytest

from app.agents.specialist.readable import graded_content
from app.agents.specialist.thyroid import ThyroidSpecialistAgent
from app.models.schemas import Organ

pytestmark = pytest.mark.track_agt


class TestGradedContent:
    def test_no_detail_keeps_legacy_behavior(self):
        """不传 detail → 与旧版逐字相同（肝脏等器官走这条，零影响）。"""
        out = graded_content(Organ.THYROID, "TR4", "理由")
        assert out == "甲状腺 · TR4 — 理由"

    def test_detail_with_different_code_replaces_head(self):
        """体系码与主级不同 → 用体系码并标注体系名。"""
        detail = {"system": "C-TIRADS 2021", "code": "4A类", "major": "TR4"}
        out = graded_content(Organ.THYROID, "TR4", "理由", detail=detail)
        assert out == "甲状腺 · 4A类（C-TIRADS 2021） — 理由"
        assert "TR4" not in out.split("—")[0], "标题不得再出现主级码"

    def test_detail_same_code_keeps_legacy(self):
        """体系码与主级相同 → 保持旧观感，不拼无意义的长串。"""
        detail = {"system": "ACR TI-RADS 2017", "code": "TR4", "major": "TR4"}
        out = graded_content(Organ.THYROID, "TR4", "理由", detail=detail)
        assert out == "甲状腺 · TR4 — 理由"

    def test_no_reason(self):
        detail = {"system": "C-TIRADS 2021", "code": "4A类"}
        assert graded_content(Organ.THYROID, "TR4", None, detail=detail) == (
            "甲状腺 · 4A类（C-TIRADS 2021）"
        )


class TestBuildGradingDetail:
    def test_extracts_ctirads_and_acr(self):
        d = ThyroidSpecialistAgent._build_grading_detail(
            "分级: 4A类\nACR TI-RADS 2017 对照：… → TR4", "TR4"
        )
        assert d["system"] == "C-TIRADS 2021"
        assert d["code"] == "4A类"
        assert d["authority"] == "中华医学会超声医学分会"
        assert d["comparison"]["code"] == "TR4"
        assert d["comparison"]["system"] == "ACR TI-RADS 2017"

    def test_falls_back_to_major_when_no_system_code(self):
        """解析不出体系码 → 退回主级，调用方无需处理 None。"""
        d = ThyroidSpecialistAgent._build_grading_detail("模糊输出", "TR3")
        assert d == {"system": "TI-RADS", "code": "TR3", "major": "TR3"}

    def test_only_acr(self):
        d = ThyroidSpecialistAgent._build_grading_detail("结论：TR5", "TR5")
        # 没有 C-TIRADS 类码 → 保持 TI-RADS 体系，不造 comparison
        assert d["system"] == "TI-RADS"
        assert "comparison" not in d

    def test_ctirads_without_acr_comparison(self):
        d = ThyroidSpecialistAgent._build_grading_detail("分级: 5类", "TR5")
        assert d["system"] == "C-TIRADS 2021"
        assert d["code"] == "5类"
        assert "comparison" not in d


class TestResponseContract:
    def test_agent_response_has_field(self):
        from app.agents.protocols import AgentResponse

        r = AgentResponse(agent_name="t")
        assert r.grading_detail is None, "默认 None，不破坏既有构造"

    def test_field_accepts_dict(self):
        from app.agents.protocols import AgentResponse

        r = AgentResponse(
            agent_name="t",
            grading_detail={"system": "C-TIRADS 2021", "code": "4A类"},
        )
        assert r.grading_detail["code"] == "4A类"
