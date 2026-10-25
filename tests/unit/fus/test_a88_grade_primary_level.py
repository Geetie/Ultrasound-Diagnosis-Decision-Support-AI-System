"""A.88 反证腿：分级抽取必须走"主级"，不能"取正文最后一个数字"。

## 为什么要单独钉这一条

A.88 A2 把 ``FusionRequest.prior_diagnosis`` 真正接上之前，``ConsistencyChecker``
的"与既往比"分支在生产上**不可达** —— 所以下面这个缺陷可以长期潜伏而无人发现：

真实结论正文（服务器 DB 原样）::

    甲状腺 · TR2 — 分级: TR2 总分: 2 置信度: 0.85 理由: 囊性…

旧实现 ``_extract_simple_grade`` 取"最后一个数字" = **0.85 → "85"**，
与既往主级 ``"2"`` 相比 ``|2-85| = 83 >= critical(2)`` →
**每次复诊都判"分级严重不一致"**（CRITICAL → needs_review）。

这是"接线把一条死路径变成活路径、随即暴露出下游的错口径"的典型案例：
修 A2 之后如果不动这里，等于亲手打开一个每次复诊都误报的开关。

## 断言策略

* 用**真实正文样本**（非手写理想样本）钉住主级 = 2；
* 用真真实的"同一次诊断与自身比较"钉住 diff = 0（这是复诊最常见的场景）；
* 钉住"无法判断"不得被解析成某个数字（0 分/置信度都不是分级）；
* 钉住"无器官上下文时不猜族"（返回 None = 未评估，而不是随手取个数字）。
"""
from __future__ import annotations

import pytest

from app.fusion.consistency import ConsistencyChecker, _extract_simple_grade, _grade_diff
from app.fusion.protocols import ConsistencySeverity, FusionRequest
from app.models.schemas import Organ
from tests.unit.fus.conftest import make_response

#: 服务器 DB 里真实存在的三类正文（2026-10-25 从 diagnosis_results 取原文）
REAL_THYROID_TR2 = "甲状腺 · TR2 — 分级: TR2 总分: 2 置信度: 0.85 理由: 囊性或实性为主、形态规则"
REAL_THYROID_UNKNOWN = "甲状腺 · TR3 — 分级: 无法判断 置信度: 0.0 理由: 现有知识未涉及具体的影像特征"


class TestPrimaryLevelNotLastNumber:
    def test_真实正文取到主级而不是置信度(self):
        """反证腿：改回 `findall(...)[-1]` 时这里得到 "85"，用例立刻红。"""
        assert _extract_simple_grade(REAL_THYROID_TR2, "thyroid") == "2"

    def test_同一份正文与自身比较级差为零(self):
        """复诊最常见场景：这次和上次是同一份口径 → 不得报任何不一致。"""
        grade = _extract_simple_grade(REAL_THYROID_TR2, "thyroid")

        assert _grade_diff(grade, grade, "thyroid") == 0

    def test_真实相邻两级相差一(self):
        """TR2 → TR3 是"一级之差"（WARNING 档），不是 83。"""
        assert _grade_diff("TR3", REAL_THYROID_TR2, "thyroid") == 1

    def test_置信度小数字不会伪装成分级(self):
        """0.85 / 0.9 这类小数里的数字不得进入分级比较。"""
        for text in (
            "分级: TR4 置信度: 0.95",
            "分级: TR4 置信度: 0.65",
        ):
            assert _extract_simple_grade(text, "thyroid") == "4"


class TestMissingGradeStaysMissing:
    def test_无法判断样本不得抽成置信度碎片(self):
        """⚠️ 这是**真实存在但自相矛盾**的样本，只断言"不再是多位数"。

        `REAL_THYROID_UNKNOWN` 的标题写着 `TR3`、字段却写 `分级: 无法判断`。
        `extract_grading_severest`（单一事实源）在"显式分级段里没有可解析分级"时
        会回退到全文扫描，于是抽到标题的 `TR3` → 返回 `"3"`。

        本用例**不**主张哪种读法对（那是 SSOT 的口径问题，动它要连带改专科结论
        与评测三条线，超出本轮范围，已在 16 号附录 A.88 登记）。这里只钉住本轮的
        核心不变量：**绝不能是 `0.85 → "85"` 这类多位数置信度碎片**。
        """
        got = _extract_simple_grade(REAL_THYROID_UNKNOWN, "thyroid")

        assert got is None or (len(got) == 1 and got.isdigit())

    def test_无器官且非规范码时返回None(self):
        """无器官 = 无法选分级族 → 明确返回 None（未评估），不猜。"""
        assert _extract_simple_grade(REAL_THYROID_TR2) is None

    def test_空文本返回None(self):
        assert _extract_simple_grade("", "thyroid") is None


class TestGradeCodeForms:
    @pytest.mark.parametrize(
        "code,organ,expected",
        [
            ("TR3", "thyroid", "3"),
            ("TR4a", "thyroid", "4"),
            ("4b", "thyroid", "4"),
            ("LR-5", "liver", "5"),
            ("lr-3", "liver", "3"),
            ("4", "thyroid", "4"),
        ],
    )
    def test_规范码按主级归一(self, code, organ, expected):
        """`TR4a` 与 `4b` 同主级 → 级差 0（此前会"一个能解析、一个 None"）。"""
        assert _extract_simple_grade(code, organ) == expected

    def test_同主级不同次码级差为零(self):
        assert _grade_diff("TR4a", "4b", "thyroid") == 0

    def test_心脏LVEF按严重度归一(self):
        """cardiac 的分级族是 normal/mild/moderate/severe（0..3），不是 LVEF 数值本身。"""
        assert _extract_simple_grade("LVEF 65%", "cardiac") == "0"  # normal
        assert _extract_simple_grade("LVEF 30%", "cardiac") == "2"  # moderate/severe 侧


class TestEndToEndNoFalseCritical:
    """整条链路：真实正文 + 同分级的既往 → 不得 CRITICAL。"""

    @pytest.mark.asyncio
    async def test_同分级既往不触发任何告警(self):
        checker = ConsistencyChecker(rag_service=None)
        response = make_response(
            organ=Organ.THYROID, content=REAL_THYROID_TR2, confidence=0.85
        )
        request = FusionRequest(
            agent_responses=[response],
            prior_diagnosis={"organ": "thyroid", "grade": "2", "confidence": 0.85},
        )

        result = await checker.check(request)

        assert result.compared_with_prior is True
        assert result.severity == ConsistencySeverity.INFO
        assert "prior_grade_diff" not in result.detail

    @pytest.mark.asyncio
    async def test_真升两级仍然报CRITICAL(self):
        """反向：真有不一致时必须照报（别把假阳性修成假阴性）。"""
        checker = ConsistencyChecker(rag_service=None)
        response = make_response(
            organ=Organ.THYROID, content="甲状腺 · TR5 — 分级: TR5 置信度: 0.85", confidence=0.85
        )
        request = FusionRequest(
            agent_responses=[response],
            prior_diagnosis={"organ": "thyroid", "grade": "3", "confidence": 0.85},
        )

        result = await checker.check(request)

        assert result.severity == ConsistencySeverity.CRITICAL
        assert "prior_grade_diff_critical" in result.detail
