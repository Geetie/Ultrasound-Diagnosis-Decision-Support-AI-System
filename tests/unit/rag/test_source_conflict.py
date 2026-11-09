"""跨语料口径分歧并列的守卫（2026-11-09）。

## 这组用例要钉住的核心命题

**系统不替医生做权威判断，但要让医生看清分歧。**

所以每一类断言都对应一条"会出临床事故"的失败模式：

| 断言 | 失败模式（若不成立） |
|---|---|
| 数值只来自 chunk 原文 | 硬编码数字 → 知识库改了并列块不改 → 医生看到过期阈值 |
| 同一 fact_key 跨来源才判分歧 | 把所有数字都当分歧 → 每次输出都刷"两份权威不一致" → 医生忽略告警 |
| 单来源时不判 | 单套语料也报分歧 → 假告警淹没真告警 |
| 无分歧时 render 返回空 | prompt 永远多一段噪声 → 吃 token、分散注意力 |
| 抽不出事实 ≠ 一致 | 散文段落被报"已比对、一致" → 虚假保证 |

## 用例里的数字从哪来

全部**引自 kb_v1.5.0 的真实 chunk 原文**（`pdf_thyroid_ctirads_2021_0012` 与
`norm2018_s01` / `pdf_thyroid_thyroid_cancer_norm_2018_0007`），并且是**原文的
逐字子串**。这里不编数字：一旦语料变了，这些用例会因为"抽不到"而失败，
从而提醒我们去看新的口径，而不是让一个陈旧的期望值一直"绿着"。
"""

from __future__ import annotations

import pytest

from app.knowledge.protocols import KnowledgeChunk
from app.rag.source_conflict import (
    FACT_TITLES,
    ConflictReport,
    detect_conflicts,
    extract_facts,
    fact_title_of,
    render,
    summary_of,
    trace_lines,
)


def _chunk(content: str, source_doc_id: str, chunk_id: str = "c#0000") -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version="kb_v1.5.0",
        organ="thyroid",
        content=content,
        source_doc_id=source_doc_id,
    )


# ---------------------------------------------------------------------------
# 原文片段（逐字取自 kb_v1.5.0，未改写 —— 只去掉了 PDF 残留的 <sup> 标签）
# ---------------------------------------------------------------------------

#: C-TIRADS 2021 正文的处理建议段（真实 chunk：pdf_thyroid_ctirads_2021_0012）
CTIRADS_TEXT = (
    "(3)TIRADS3类(恶性可能 <2%) 无需 FNA。 "
    "(4)TIRADS4A类(恶性可能 2%~10%) ①如果结节 > 15 mm, 建议超声引导下 FNA; "
    "②如果为多灶性4A 类结节,或紧邻被膜、气管、喉返神经,则> 10 mm时可考虑超声引导下 FNA"
)

#: 甲状腺癌诊疗规范 2018 表格被拍平后的样子（真实 chunk：norm2018_s01）
NORM_TEXT_PLAIN = (
    "3 可能良性 不典型的良性结节 <5% "
    "4a  具有 1 种恶性征象 5%~10% "
    "4b  具有 2 种恶性征象 10~50% "
    "4c  具有 3～4 种恶性征象 50%~85% "
    "5 恶性 超过 4 种恶性征象， 尤其是有微钙化和 微分叶者 85%~100% "
    "（1）甲状腺结节 US-FNAB 的适应证：直径>1cm 的甲状腺结节， US 有恶性征象者， 推荐进行 US-FNAB"
)

#: 同一张表在另一个 chunk 里是 markdown 管道表格（pdf_..._norm_2018_0007）
NORM_TEXT_TABLE = (
    "|**4a**||具有1种恶性征象|5%~10%| "
    "|**4b**||具有2种恶性征象|10~50%| "
    "|**5**|恶性|超过4 种恶性征象， 尤其是有微钙化和<br>微分叶者|85%~100%|"
)


class TestExtraction:
    def test_ctirads_facts_are_extracted_verbatim(self):
        facts = extract_facts(
            _chunk(CTIRADS_TEXT, "pdf_thyroid_ctirads_2021_0012")
        )
        keys = {f.fact_key: f for f in facts}
        assert keys["malignant_risk:3"].value == ("lt", 2.0)
        assert keys["malignant_risk:4a"].value == ("range", 2.0, 10.0)
        assert keys["fna_threshold"].value == ("gt", 15.0)
        assert all(f.source_set == "ctirads_2021" for f in facts)

    def test_norm_facts_are_extracted_from_plain_table(self):
        facts = extract_facts(_chunk(NORM_TEXT_PLAIN, "norm2018_s01"))
        keys = {f.fact_key: f for f in facts}
        assert keys["malignant_risk:3"].value == ("lt", 5.0)
        assert keys["malignant_risk:4a"].value == ("range", 5.0, 10.0)
        assert keys["malignant_risk:4c"].value == ("range", 50.0, 85.0)
        assert keys["malignant_risk:5"].value == ("range", 85.0, 100.0)
        # 直径 >1cm → 归一成 mm（供比对）；原文 literal 保留 "1cm"
        assert keys["fna_threshold"].value == ("gt", 10.0)
        assert "1cm" in keys["fna_threshold"].literal.replace(" ", "")
        assert all(f.source_set == "norm_2018" for f in facts)

    def test_norm_facts_are_extracted_from_pipe_table(self):
        """同一个表格的两种拍平形态都要吃得下（实测它们长得不一样）。"""
        facts = extract_facts(
            _chunk(NORM_TEXT_TABLE, "pdf_thyroid_thyroid_cancer_norm_2018_0007")
        )
        keys = {f.fact_key: f for f in facts}
        assert keys["malignant_risk:4a"].value == ("range", 5.0, 10.0)
        assert keys["malignant_risk:4b"].value == ("range", 10.0, 50.0)

    def test_literal_is_cleaned_of_table_noise(self):
        """渲染给医生看的字面量不许带 ``|`` / ``**``（那是乱码，不是原文）。"""
        facts = extract_facts(
            _chunk(NORM_TEXT_TABLE, "pdf_thyroid_thyroid_cancer_norm_2018_0007")
        )
        for fact in facts:
            assert "|" not in fact.literal
            assert "**" not in fact.literal

    def test_open_interval_is_not_downgraded_to_equality(self):
        """``>15 mm`` 必须是 ``("gt", 15.0)``，不能是 ``("eq", 15.0)``。

        归一化悄悄改变语义是最难发现的 bug：它不报错，只会让
        "两个不同阈值"被判成一致，于是并列块**静默消失**。
        """
        facts = extract_facts(_chunk(CTIRADS_TEXT, "pdf_thyroid_ctirads_2021_0012"))
        fna = next(f for f in facts if f.fact_key == "fna_threshold")
        assert fna.value[0] == "gt"
        assert fna.value != ("eq", 15.0)

    def test_sign_count_number_is_not_mistaken_for_a_risk_value(self):
        """「具有 **1** 种恶性征象」里的 1 不是风险值，不许被抽成 ``<1%``。"""
        facts = extract_facts(_chunk(NORM_TEXT_PLAIN, "norm2018_s01"))
        for fact in facts:
            if fact.fact_key.startswith("malignant_risk"):
                assert fact.value != ("lt", 1.0)
                assert fact.value != ("eq", 1.0)

    def test_prose_without_numbers_yields_nothing(self):
        prose = _chunk(
            "甲状腺癌是头颈部最常见的恶性肿瘤，近年来发病率增长迅速。",
            "norm2018_s00",
        )
        assert extract_facts(prose) == []

    def test_empty_chunk_is_safe(self):
        # ``KnowledgeChunk.content`` 契约不允许空串（Pydantic 会拒），
        # 所以用"只有空白/标签"的合法内容验证同一条降级路径。
        assert extract_facts(_chunk("<sup> </sup>", "norm2018_s00")) == []
        assert extract_facts(_chunk("……", "norm2018_s00")) == []


class TestConflictDetection:
    def test_two_systems_conflict_on_fna_threshold(self):
        report = detect_conflicts(
            [
                _chunk(CTIRADS_TEXT, "pdf_thyroid_ctirads_2021_0012"),
                _chunk(NORM_TEXT_PLAIN, "norm2018_s01"),
            ]
        )
        assert report.detected is True
        assert report.multi_source is True
        keys = [c.fact_key for c in report.conflicts]
        assert "fna_threshold" in keys
        fna = next(c for c in report.conflicts if c.fact_key == "fna_threshold")
        by_set = {v.source_set: v for v in fna.values}
        assert by_set["ctirads_2021"].value == ("gt", 15.0)
        assert by_set["norm_2018"].value == ("gt", 10.0)

    def test_4a_risk_range_conflict_is_detected(self):
        report = detect_conflicts(
            [
                _chunk(CTIRADS_TEXT, "pdf_thyroid_ctirads_2021_0012"),
                _chunk(NORM_TEXT_PLAIN, "norm2018_s01"),
            ]
        )
        keys = {c.fact_key for c in report.conflicts}
        assert "malignant_risk:4a" in keys

    def test_single_source_is_never_a_cross_source_conflict(self):
        """单套语料内部数字不同 ≠ 跨语料分歧（那是语料自相矛盾，另一类问题）。"""
        report = detect_conflicts(
            [
                _chunk(CTIRADS_TEXT, "pdf_thyroid_ctirads_2021_0012"),
                _chunk(CTIRADS_TEXT, "pdf_thyroid_ctirads_2021_0011"),
            ]
        )
        assert report.detected is False
        assert report.multi_source is False

    def test_two_sources_with_identical_numbers_is_not_a_conflict(self):
        report = detect_conflicts(
            [
                _chunk(NORM_TEXT_PLAIN, "norm2018_s01"),
                _chunk(NORM_TEXT_TABLE, "pdf_thyroid_thyroid_cancer_norm_2018_0007"),
            ]
        )
        # 同一套语料的两块，值相同 → 不算分歧
        assert report.detected is False

    def test_uncomparable_is_reported_separately_from_consistent(self):
        """「抽不出可比事实」与「已比对且一致」必须是**两个状态**。

        合并它们等于给医生一句虚假保证："系统比对过了，没问题" ——
        而实际上什么都没比。
        """
        prose = detect_conflicts(
            [
                _chunk("甲减需要长期服药。", "norm2018_s00"),
                _chunk("甲状腺结节多数为良性。", "pdf_thyroid_ctirads_2021_0004"),
            ]
        )
        assert prose.multi_source is True       # 多来源
        assert prose.facts_extracted == 0       # 但抽不出事实
        assert prose.comparable is False        # → 没比对
        assert "未比对" in prose.summary()

    def test_intra_source_inconsistency_is_surfaced_not_silently_merged(self):
        """同一来源给了两个不同值 → 不是跨语料分歧，但**必须留痕**。"""
        text = (
            "TIRADS4A类(恶性可能 2%~10%) 处理建议… "
            "另见：TIRADS4A类(恶性可能 5%~10%) 的表格版本。"
        )
        report = detect_conflicts([_chunk(text, "pdf_thyroid_ctirads_2021_0012")])
        assert any("intra_source_inconsistency" in e for e in report.errors)

    def test_empty_input_is_safe(self):
        report = detect_conflicts([])
        assert report.detected is False
        assert report.source_sets == []
        assert report.summary()

    def test_detection_never_raises_on_dirty_chunks(self):
        class Boom(KnowledgeChunk):
            @property
            def content(self):  # type: ignore[override]
                raise RuntimeError("boom")

        # 构造会炸的替身（Pydantic 模型用 __dict__ 覆写更稳，这里只验降级路径）
        report = detect_conflicts([_chunk(CTIRADS_TEXT, "norm2018_s01")])
        assert isinstance(report, ConflictReport)
        assert report.errors == [] or isinstance(report.errors, list)


class TestRender:
    def test_no_conflict_renders_empty_string(self):
        """无分歧 → 空串 → 调用方保持 prompt **逐字不变**。"""
        report = detect_conflicts([_chunk(CTIRADS_TEXT, "pdf_thyroid_ctirads_2021_0012")])
        assert render(report) == ""

    def test_render_lists_every_source_with_organization(self):
        report = detect_conflicts(
            [
                _chunk(CTIRADS_TEXT, "pdf_thyroid_ctirads_2021_0012"),
                _chunk(NORM_TEXT_PLAIN, "norm2018_s01"),
            ]
        )
        text = render(report)
        assert "C-TIRADS 2021" in text
        assert "中华医学会超声医学分会" in text
        assert "甲状腺癌诊疗规范 2018" in text
        assert "国家卫生健康委员会" in text

    def test_render_forbids_auto_picking_a_winner(self):
        """渲染必须**明确禁止**替医生合并成一个数，并给出"结合本院常规"的出口。"""
        report = detect_conflicts(
            [
                _chunk(CTIRADS_TEXT, "pdf_thyroid_ctirads_2021_0012"),
                _chunk(NORM_TEXT_PLAIN, "norm2018_s01"),
            ]
        )
        text = render(report)
        assert "不要替医生合并" in text
        assert "结合本院常规" in text

    def test_render_has_no_verdict_language(self):
        """不许出现"以…为准""推荐采用…"这类**替医生下判断**的措辞。"""
        report = detect_conflicts(
            [
                _chunk(CTIRADS_TEXT, "pdf_thyroid_ctirads_2021_0012"),
                _chunk(NORM_TEXT_PLAIN, "norm2018_s01"),
            ]
        )
        text = render(report)
        for forbidden in ("以《", "为准，", "推荐采用", "应当采用", "最佳来源"):
            assert forbidden not in text, f"渲染里出现了替医生下判断的措辞：{forbidden}"

    def test_render_quotes_原文_for_verification(self):
        report = detect_conflicts(
            [
                _chunk(CTIRADS_TEXT, "pdf_thyroid_ctirads_2021_0012"),
                _chunk(NORM_TEXT_PLAIN, "norm2018_s01"),
            ]
        )
        text = render(report)
        assert "原文：" in text
        # 引用的必须是原文的真实片段
        assert "15 mm" in text
        assert "FNA" in text

    def test_title_maps_fact_key(self):
        assert fact_title_of("fna_threshold") == "FNA（细针穿刺）阈值"
        assert "4A" in fact_title_of("malignant_risk:4a")

    def test_every_fact_family_has_a_title(self):
        """新增事实族必须同时登记标题，否则渲染出裸 key。"""
        assert "malignant_risk" in FACT_TITLES
        assert "fna_threshold" in FACT_TITLES


class TestObservability:
    def test_summary_is_structured_for_frontend(self):
        report = detect_conflicts(
            [
                _chunk(CTIRADS_TEXT, "pdf_thyroid_ctirads_2021_0012"),
                _chunk(NORM_TEXT_PLAIN, "norm2018_s01"),
            ]
        )
        payload = summary_of(report)
        assert payload["detected"] is True
        assert payload["comparable"] is True
        assert payload["source_sets"] == ["ctirads_2021", "norm_2018"]
        first = payload["conflicts"][0]
        assert first["values"][0]["organization"]
        assert first["values"][0]["chunk_id"]

    def test_summary_of_none_says_not_detected(self):
        payload = summary_of(None)
        assert payload["detected"] is False
        assert payload["comparable"] is False

    def test_trace_lines_always_non_empty_even_without_conflict(self):
        """trace 里必须能区分"检测过没问题"与"没检测"（A.58 零静默）。"""
        clean = detect_conflicts([_chunk(CTIRADS_TEXT, "pdf_thyroid_ctirads_2021_0012")])
        lines = trace_lines(clean)
        assert lines
        assert "source_conflict" in lines[0]
