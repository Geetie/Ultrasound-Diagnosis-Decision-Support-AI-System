"""A.95 Q33-②：文档解析质量基线的**判据本身**守卫。

## 背景

Q33-② 原计划「引入 RAGFlow DeepDoc 解析层」。真机核对发现两条硬约束：
1. **服务器访问不了 github.com**（`curl` 超时）→ 装不了 RAGFlow；
2. DeepDoc **不是 pip 包**（`import deepdoc` → ModuleNotFoundError），
   要 onnxruntime + 版面分析/表格识别/OCR 三套 CV 模型（≈2GB）+ 独立服务。

**所以"换解析器"本轮做不了。** 但有一件必须前置的事：
给现有解析器建**可量化基线**，否则将来换了解析器也无法判断"是不是变好了"。

## 本守卫钉什么

1. `analyze_text` 的指标必须**真的能反映问题**（反证：构造乱码/空文本 → 判 SUSPECT/EMPTY）；
2. `judge` 的三条判据必须各自能触发（反证：逐条构造违规输入）；
3. 中文占比、乱码率、每页字数三个阈值必须与 docstring 一致；
4. **不许把"没接入 DeepDoc"写成"已接入"** —— 报告里必须保留 `deepdoc_status` 说明。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.audit.p72_doc_parse_baseline import (
    MAX_REPLACEMENT_RATIO,
    MIN_CHARS_PER_PAGE,
    MIN_CJK_RATIO,
    analyze_text,
    judge,
)

pytestmark = [pytest.mark.track_quality]

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "audit" / "p72_doc_parse_baseline.py"


class TestMetricsReflectProblems:
    def test_empty_text_flagged(self):
        m = analyze_text("", 10)
        assert m["empty"] is True
        status, _ = judge(m)
        assert status == "EMPTY"

    def test_garbled_text_detected(self):
        """大量 \ufffd = 编码损坏，必须被 replacement_ratio 抓住。"""
        text = "\ufffd" * 500 + "中文" * 10
        m = analyze_text(text, 5)
        assert m["replacement_char_count"] == 500
        assert m["replacement_ratio"] > MAX_REPLACEMENT_RATIO
        status, issues = judge(m)
        assert status == "SUSPECT"
        assert any("乱码" in i for i in issues)

    def test_english_only_detected(self):
        """纯英文页（抽错对象）→ cjk_ratio 低 → SUSPECT。"""
        m = analyze_text("This is English text. " * 100, 5)
        assert m["cjk_ratio"] < MIN_CJK_RATIO
        status, issues = judge(m)
        assert status == "SUSPECT"
        assert any("中文占比" in i for i in issues)

    def test_too_few_chars_per_page(self):
        """扫描件抽不出字 → 每页字数太低 → SUSPECT。"""
        m = analyze_text("中文" * 50, 20)  # 100 字 / 20 页 = 5 字每页
        assert m["chars_per_page"] < MIN_CHARS_PER_PAGE
        status, _issues = judge(m)
        assert status == "SUSPECT"

    def test_healthy_doc_passes(self):
        text = ("甲状腺癌诊疗指南 一、概述\n" + "中文正文内容。" * 300) * 3
        m = analyze_text(text, 10)
        status, issues = judge(m)
        assert status == "PASS", issues


class TestStructuralMetrics:
    def test_counts_headings(self):
        text = "# 标题\n一、章节\n1. 小节\n普通正文\n" * 5
        m = analyze_text(text, 1)
        assert m["heading_lines"] >= 15, m["heading_lines"]

    def test_counts_table_lines(self):
        text = "| a | b |\n|---|---|\n| 1 | 2 |\n" * 5
        m = analyze_text(text, 1)
        assert m["table_lines"] >= 15

    def test_counts_page_number_noise(self):
        text = "\n".join(["123", "456", "中文正文"] * 10)
        m = analyze_text(text, 1)
        assert m["page_number_noise"] > 0.5

    def test_counts_duplicate_lines(self):
        text = "\n".join(["水印行"] * 10 + ["正文"])
        m = analyze_text(text, 1)
        assert m["dup_line_ratio"] > 0.5


class TestHonesty:
    def test_deepdoc_status_recorded(self):
        """反证腿：把 `deepdoc_status` 删掉 → 本用例红。

        必须留下"为什么没接入"的记录，否则后来者会以为已接入。
        """
        src = _SCRIPT.read_text(encoding="utf-8")
        assert "deepdoc_status" in src, "报告缺 deepdoc_status —— 会让人以为已接入 DeepDoc"
        assert "github.com" in src, "没写清服务器访问不了 GitHub 这条约束"
        assert "不是 pip 包" in src or "ModuleNotFoundError" in src, (
            "没写清 DeepDoc 不是 pip 包这条约束"
        )

    def test_no_false_claim_of_migration(self):
        """不许出现"已接入 DeepDoc"这类假声明。"""
        src = _SCRIPT.read_text(encoding="utf-8")
        for bad in ("已接入 DeepDoc", "已迁移到 DeepDoc", "DeepDoc 已上线"):
            assert bad not in src, f"出现假声明: {bad}"

    def test_thresholds_documented(self):
        """三个阈值必须在 docstring 里写明（否则改了没人知道为什么）。"""
        src = _SCRIPT.read_text(encoding="utf-8")
        assert "MIN_CJK_RATIO" in src and "0.30" in src
        assert "MIN_CHARS_PER_PAGE" in src and "200" in src
