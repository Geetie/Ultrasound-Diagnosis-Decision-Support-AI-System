"""A.93 守卫：分级口径分歧——**标题与理由不许静默矛盾**。

## 这个"分歧"到底是什么（真机取证，2026-10-25）

用户看到的形态：

    甲状腺 · TR4 — 分级: 无法判断 置信度: 0.0 理由: 当前信息不足以进行 TI-RADS 分级…

标题说 **TR4**，正文说 **"分级: 无法判断"** —— 同一行文字里自相矛盾。
统计：thyroid 299 条里 **59 条（19%）** 是这个形态。

拆开看是两个独立问题的叠加：

### 问题 1：`_extract_reason` 把整段原文当理由（已修）

模型常把多个字段塞成**一行**：
`分级: 无法判断 置信度: 0.0 理由: xxx 证据: 无 患者解释: …`
原实现只处理"行首就是`理由:`"，
对"行中出现`理由:`"直接 `return line_s`（整行）→ 理由字段吞掉了
`分级: 无法判断 置信度: 0.0 …`，于是正文里出现了第二个"分级"。

修法：行中出现 `理由:` 时取其后内容，并在**下一个字段名**处截断
（`_cut_at_next_field`）。真机实测：83 字 → 33 字，只剩真正的理由。

### 问题 2：分级来自结构化重试，理由来自首次输出（**如实标注**）

真机日志：
```
thyroid_grade_not_detected  llm_raw_len=111   ← 首次输出抽不到分级
thyroid_grade_extracted     grade=TR4 llm_raw_len=111  ← 重试给了 TR4
```
两次 `llm_raw_len` 相同 → `_parse_response(首次文本, grade_override="TR4")`
把两者**合并**（这是 `base.invoke` 5b 的有意设计：重试只为救回分级，
富文本仍取首次）。

**但不能静默并用**：医生看到"TR4 + 信息不足"无法判断该信哪个。
所以当理由含"无法判断/信息不足/不足以…"且分级来自重试时，
正文必须**显式注明分级来源与依据不足**，并保持转人工。

## 本守卫钉什么

1. `_extract_reason` 不再把整段当理由（单行输入 → 只留理由）；
2. 理由在下一个字段名处截断（证据/置信度/患者解释…不得混入）；
3. 多行正常输入不受影响；
4. 矛盾时必须标注来源（反证：去掉标注 → 红）；
5. 不矛盾时不加标注（避免每条都带噪音）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.specialist.thyroid import ThyroidSpecialistAgent

pytestmark = [pytest.mark.track_agt]

_REPO = Path(__file__).resolve().parents[3]
_THYROID = _REPO / "app" / "agents" / "specialist" / "thyroid.py"

#: 真机实际出现的一行式输出
ONE_LINE = (
    "分级: 无法判断 置信度: 0.0 理由: 当前信息不足以进行 TI-RADS 分级，"
    "缺少必要的影像特征描述。 证据: 无 患者解释: 请根据医生建议进行进一步检查。"
)


class TestExtractReason:
    def test_single_line_keeps_only_reason(self):
        """反证腿：还原成 `return line_s` → 本用例红（会返回 83 字整段）。"""
        out = ThyroidSpecialistAgent._extract_reason(ONE_LINE, "TR4")
        assert "分级:" not in out, f"理由里混入了「分级:」字段：{out!r}"
        assert "置信度" not in out, f"理由里混入了「置信度」：{out!r}"
        assert "证据" not in out, f"理由里混入了「证据」：{out!r}"
        assert out.startswith("当前信息不足以进行"), out
        assert len(out) < 60, f"理由过长（{len(out)} 字），像是整段原文"

    def test_multiline_unchanged(self):
        out = ThyroidSpecialistAgent._extract_reason(
            "分级: TR4\n理由: 实性低回声伴微钙化\n证据: [1], [2]", "TR4"
        )
        assert out == "实性低回声伴微钙化"

    def test_leading_reason_prefix(self):
        out = ThyroidSpecialistAgent._extract_reason(
            "理由: 边界不清、纵横比>1\n证据: [1]", "TR4"
        )
        assert out == "边界不清、纵横比>1"

    def test_cut_at_patient_explanation(self):
        out = ThyroidSpecialistAgent._extract_reason(
            "理由: 微钙化 患者解释: 请不必担心", "TR4"
        )
        assert "患者解释" not in out
        assert "微钙化" in out

    def test_empty_when_no_reason(self):
        assert ThyroidSpecialistAgent._extract_reason("分级: TR4", "TR4") == ""


class TestConflictIsLabelled:
    """分级来自重试、理由说"信息不足" → 必须标注来源。"""

    def test_source_is_declared_when_conflicting(self):
        """反证腿：删掉标注逻辑 → 本用例红（正文会静默并列 TR4 与"信息不足"）。"""
        src = _THYROID.read_text(encoding="utf-8")
        assert "reason_conflicts" in src, "没有检测「理由与分级矛盾」"
        assert "来自补充的结构化重试" in src, "矛盾时没有注明分级来源"
        assert "请人工复核" in src, "矛盾时没有提示人工复核"

    def test_conflict_keywords_cover_live_wording(self):
        src = _THYROID.read_text(encoding="utf-8")
        for kw in ("无法判断", "信息不足", "不足以"):
            assert kw in src, f"矛盾判定缺关键词 {kw}（真机实际用词）"

    def test_only_when_override_present(self):
        """只在 `grade_override`（= 分级来自重试）时才标注。

        否则首次输出就带分级的正常病例也会被加一堆噪音。
        """
        src = _THYROID.read_text(encoding="utf-8")
        assert "reason_conflicts and grade_override" in src, (
            "标注条件应同时要求「矛盾」与「分级来自重试」"
        )


class TestGradingFieldNotDuplicated:
    def test_content_has_single_grading_mention(self):
        """真机原句走 `_extract_reason` + `graded_content` 后，正文只应有一个分级。"""
        from app.agents.specialist.readable import graded_content
        from app.models.schemas import Organ

        reason = ThyroidSpecialistAgent._extract_reason(ONE_LINE, "TR4")
        content = graded_content(Organ.THYROID, "TR4", reason)
        # 标题里一个 "TR4"，正文不许再出现第二个 "分级:"
        assert content.count("分级:") == 0, f"正文里出现了多余的「分级:」：{content}"
        assert content.startswith("甲状腺 · TR4")
