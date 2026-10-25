"""A.95 Q33-②：文档解析质量**基线评测**（为 DeepDoc 对照做前置）。

## 为什么先做这个而不是直接接 DeepDoc

15 号 P1 的 Q33-② 写的是「引入 RAGFlow DeepDoc 解析层（Apache 2.0），
输出仍走现有 UDT 标准化 → 分块 → 入库管线」，并给了边界
「**只取文档理解能力，不替换自研检索管线**」。

但真机核对发现两个硬约束：
1. **服务器访问不了 github.com**（`curl` 超时）→ 装不了 RAGFlow；
2. DeepDoc 不是 pip 包（`import deepdoc` → ModuleNotFoundError），
   它要 onnxruntime + 版面分析/表格识别/OCR 三套 CV 模型（约 2GB）+ 独立服务。

**所以"换解析器"这件事本轮无法完成。** 但有一件**必须前置**的事可以做，
而且不做就无法判断"换了解析器到底有没有变好"：

## 本脚本做什么：给现有解析器建立**可量化基线**

用 `pymupdf4llm`（已在服务器，1.28.2）对 `data/corpus/raw/` 的 8 份 PDF 抽取正文，
输出**结构化质量指标**，作为将来任何解析器替换的对照基准：

| 指标 | 为什么重要 |
|---|---|
| `chars` / `chars_per_page` | 抽不出字（扫描件）或抽出乱码都会异常 |
| `cjk_ratio` | 中文占比 —— 低于 0.3 说明抽的是页眉页码/乱码 |
| `replacement_char_count` | `\ufffd` 个数 = 编码损坏的直接证据 |
| `heading_lines` | 识别出的标题行数（章节结构是否保留） |
| `table_lines` | 含 `|` 的行数（表格是否保留 —— DeepDoc 的强项就在这里） |
| `page_number_noise` | 纯数字行占比（页眉页脚噪声） |
| `dup_line_ratio` | 重复行比例（水印造成） |

**判据（预注册）**：将来任何替换方案必须在这 7 项上**不劣于基线**，
且 `table_lines` 或 `heading_lines` 有显著提升 —— 否则不换。
这与 15 号「只在显著更好时换管线」一致。

## 用法

    ~/uai/venv/bin/python scripts/audit/p72_doc_parse_baseline.py
    ~/uai/venv/bin/python scripts/audit/p72_doc_parse_baseline.py --compare 新解析器输出目录

退出码：0 有基线 ｜ 2 前置缺失。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter
from datetime import datetime, timezone

RAW_DIR = pathlib.Path.home() / "uai" / "data" / "corpus" / "raw"

#: 判据（预注册）
MIN_CJK_RATIO = 0.30          # 低于此值说明抽的不是中文正文
MAX_REPLACEMENT_RATIO = 0.005  # \ufffd 占比上限
MIN_CHARS_PER_PAGE = 200       # 每页至少这么多字才算抽出来了


def analyze_text(text: str, pages: int) -> dict:
    """算一份文档的解析质量指标。**纯函数**（守卫钉住）。"""
    total = len(text or "")
    if total == 0:
        return {
            "chars": 0,
            "pages": pages,
            "chars_per_page": 0.0,
            "cjk_ratio": 0.0,
            "replacement_char_count": 0,
            "replacement_ratio": 0.0,
            "heading_lines": 0,
            "table_lines": 0,
            "page_number_noise": 0.0,
            "dup_line_ratio": 0.0,
            "empty": True,
        }

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    n_lines = max(len(lines), 1)

    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fa5")
    repl = text.count("\ufffd")

    # 标题行：Markdown 标题，或"一、二、…/1. /第X章"这类
    heading_re = re.compile(
        r"^(?:#{1,6}\s+|[一二三四五六七八九十]+[、.]\s*|\d+\.\s+|第[一二三四五六七八九十百]+[章节条])"
    )
    headings = sum(1 for ln in lines if heading_re.match(ln))

    # 表格行：Markdown 表格（含 |）
    table_lines = sum(1 for ln in lines if "|" in ln)

    # 页码噪声：纯数字行（1~4 位）
    page_noise = sum(1 for ln in lines if re.fullmatch(r"\d{1,4}", ln))

    # 重复行（水印/页眉）
    cnt = Counter(lines)
    dup = sum(v - 1 for v in cnt.values() if v > 1)

    return {
        "chars": total,
        "pages": pages,
        "chars_per_page": round(total / max(pages, 1), 1),
        "cjk_ratio": round(cjk / total, 4),
        "replacement_char_count": repl,
        "replacement_ratio": round(repl / total, 6),
        "heading_lines": headings,
        "table_lines": table_lines,
        "page_number_noise": round(page_noise / n_lines, 4),
        "dup_line_ratio": round(dup / n_lines, 4),
        "empty": False,
    }


def judge(metrics: dict) -> tuple[str, list[str]]:
    """按预注册判据判一份文档是否解析合格。返回 (状态, 问题列表)。"""
    issues: list[str] = []
    if metrics.get("empty"):
        return "EMPTY", ["正文为空（可能是扫描件，需 OCR）"]
    if metrics["cjk_ratio"] < MIN_CJK_RATIO:
        issues.append(
            f"中文占比 {metrics['cjk_ratio']:.2%} < {MIN_CJK_RATIO:.0%}（抽出来不是中文正文）"
        )
    if metrics["replacement_ratio"] > MAX_REPLACEMENT_RATIO:
        issues.append(
            f"乱码字符占比 {metrics['replacement_ratio']:.3%} > {MAX_REPLACEMENT_RATIO:.1%}"
        )
    if metrics["chars_per_page"] < MIN_CHARS_PER_PAGE:
        issues.append(
            f"每页仅 {metrics['chars_per_page']:.0f} 字 < {MIN_CHARS_PER_PAGE}"
        )
    return ("PASS" if not issues else "SUSPECT", issues)


def extract_with_pymupdf4llm(pdf: pathlib.Path) -> tuple[str, int]:
    """用 pymupdf4llm 抽 Markdown，返回 (text, pages)。"""
    import pymupdf
    import pymupdf4llm

    with pymupdf.open(str(pdf)) as doc:
        pages = doc.page_count
    text = pymupdf4llm.to_markdown(str(pdf))
    return text or "", pages


def main(compare_dir: str | None, report: str | None) -> int:
    if not RAW_DIR.is_dir():
        print(f"[FATAL] 语料目录不存在：{RAW_DIR}")
        return 2
    pdfs = sorted(RAW_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"[FATAL] {RAW_DIR} 下没有 PDF")
        return 2

    print(f"=== 解析基线：{len(pdfs)} 份 PDF（pymupdf4llm）===")
    results: list[dict] = []
    for pdf in pdfs:
        try:
            text, pages = extract_with_pymupdf4llm(pdf)
        except Exception as exc:
            print(f"  ❌ {pdf.name}: {type(exc).__name__}: {str(exc)[:80]}")
            results.append({"file": pdf.name, "error": f"{type(exc).__name__}"})
            continue
        m = analyze_text(text, pages)
        status, issues = judge(m)
        results.append({"file": pdf.name, "status": status, "metrics": m, "issues": issues})
        print(
            f"  {status:8s} {pdf.name[:42]:44s} "
            f"{m['chars']:>7} 字 / {m['pages']:>3} 页 / "
            f"每页 {m['chars_per_page']:>6} / 中文 {m['cjk_ratio']:.0%} / "
            f"标题 {m['heading_lines']:>3} / 表格行 {m['table_lines']:>3}"
            + (f"  ⚠ {issues[0][:40]}" if issues else "")
        )

    ok = [r for r in results if r.get("status") == "PASS"]
    suspect = [r for r in results if r.get("status") == "SUSPECT"]
    empty = [r for r in results if r.get("status") == "EMPTY"]
    print(
        f"\n汇总：PASS {len(ok)} / SUSPECT {len(suspect)} / EMPTY {len(empty)} "
        f"（共 {len(results)}）"
    )

    payload = {
        "probe": "p72_doc_parse_baseline",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parser": "pymupdf4llm",
        "corpus_dir": str(RAW_DIR),
        "n_docs": len(results),
        "summary": {"pass": len(ok), "suspect": len(suspect), "empty": len(empty)},
        "pre_registered_criteria": {
            "MIN_CJK_RATIO": MIN_CJK_RATIO,
            "MAX_REPLACEMENT_RATIO": MAX_REPLACEMENT_RATIO,
            "MIN_CHARS_PER_PAGE": MIN_CHARS_PER_PAGE,
            "replacement_rule": (
                "将来任何解析器替换必须在这 7 项上不劣于基线，"
                "且 table_lines 或 heading_lines 显著提升 —— 否则不换（15 号原话）"
            ),
        },
        "deepdoc_status": (
            "Q33-② 未能接入：服务器访问不了 github.com（curl 超时），"
            "且 DeepDoc 不是 pip 包（要 onnxruntime + 版面/表格/OCR 三套 CV 模型 ≈2GB + 独立服务）。"
            "本脚本只交付**可量化的对照基线**，让将来换解析器时有判据。"
        ),
        "docs": results,
    }
    if report:
        rp = pathlib.Path(report).expanduser()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告：{rp}")

    if compare_dir:
        print(
            f"\n[--compare {compare_dir}] 对照模式需该目录下有同名 .md 文件；"
            "本脚本暂只输出基线，对照逻辑待新解析器可用后再补。"
        )
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--compare", default=None, help="新解析器输出目录（预留）")
    ap.add_argument(
        "--report",
        default=str(pathlib.Path.home() / "uai/reports/doc_parse_baseline.json"),
    )
    a = ap.parse_args()
    sys.exit(main(a.compare, a.report))
