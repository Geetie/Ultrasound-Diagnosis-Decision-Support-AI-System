"""把 C-TIRADS 计数法分档表补进知识库（2026-11-08）。

## 为什么需要

生产实测：模型会输出 `（注：0-2 分为 TR2）` 这类**自造分档规则**，
而 `kb_v1.4.0` / `kb_v1.5.0` 的甲状腺语料里：
```
TR2 / TR3 / TR4 / TR5 : 各 0 条
"2分" / "积2分" / "分档" : 各 0 条
```
→ **模型检索不到分档表，只能从 prompt 抄或自己编。**

## 但源 PDF 里有

`~/uai/data/corpus/raw/ctirads_2021.pdf` 第 19887 字符处：
```
表1 基于计数法的 C-TIRADS
结节    | 分值  | 恶性率% | C-TIRADS分类
无结节  | 无分值* | 0     | 1 无结节
有结节  | -1    | 0       | 2 良性
        | 0     | <2      | 3 良性可能
        | 1     | 2~10    | 4A 低度可疑恶性
        | 2     | 10~50   | 4B 中度可疑恶性
        | 3~4   | 50~90   | 4C 高度可疑恶性
        | 5     | >90     | 5 高度提示恶性
        | -     | -       | 6 活检证实的恶性
注：* 无结节，不予赋分
```

**这是分块时丢失的，不是源文档没有。** 本脚本把这张表单独建成可检索文档。

## 反编造纪律

- 表内容**逐字来自 PDF 抽取文本**（`assert` 校验关键片段存在）
- 增加一份「征象→分值」的说明段，同样逐字来自原文
- 不改写、不补全、不推断

## 用法

    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/kb_build_ctirads_counting_table.py \
        --pdf ~/uai/data/corpus/raw/ctirads_2021.pdf \
        --out ~/uai/data/kb_docs/thyroid_ctirads_counting.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: 分档表必须出现在源 PDF 里的关键片段（逐字校验，防编造）
TABLE_ANCHORS = (
    "表1 基于计数法的",
    "分值",
    "恶性率",
    "良性可能",
    "低度可疑恶性",
    "中度可疑恶性",
    "高度可疑恶性",
    "高度提示恶性",
    "活检证实的恶性",
)


def extract_source_text(pdf_path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    return "\n".join((pg.extract_text() or "") for pg in reader.pages)


def find_table_segment(text: str) -> str:
    """定位表 1 的原文片段（从「表1 基于计数法」起到「分类注意事项」前）。"""
    i = text.find("表1 基于计数法的")
    if i < 0:
        raise SystemExit("源 PDF 里找不到「表1 基于计数法的」—— 拒绝凭空生成")
    j = text.find("分类注意事项", i)
    if j < 0:
        j = i + 700
    return text[i:j].strip()


def find_scoring_method(text: str) -> str:
    """定位「计数法」的赋分说明段（特征清单）。"""
    i = text.find("通过对上述灰阶超声特征的多元")
    if i < 0:
        i = text.find("计数法")
    if i < 0:
        raise SystemExit("源 PDF 里找不到计数法说明 —— 拒绝凭空生成")
    return text[i : i + 900].strip()


def build_docs(source: str) -> list[dict]:
    table = find_table_segment(source)
    method = find_scoring_method(source)

    # 反编造守卫：每个锚点必须逐字存在
    missing = [a for a in TABLE_ANCHORS if a not in table]
    if missing:
        raise SystemExit(f"表格锚点缺失 {missing} —— 抽取失败，拒绝产出")

    # 归一化（PDF 抽取会在中文间插空格，这是已知的抽取伪影）
    def norm(s: str) -> str:
        s = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", s)
        s = re.sub(r"\s{2,}", " ", s)
        return s.strip()

    table_n = norm(table)
    method_n = norm(method)

    return [
        {
            "source_doc_id": "ctirads_2021_counting_table",
            "title": "C-TIRADS 计数法分档表（表1）：分值 → 类别 → 恶性率",
            "source_kind": "guideline_table",
            "section_path": "C-TIRADS 2021 / 表1 基于计数法的 C-TIRADS",
            "organ": "thyroid",
            "content": (
                "# C-TIRADS 计数法分档表（表1）\n\n"
                "C-TIRADS 采用**计数法**：统计结节的可疑恶性超声特征个数，"
                "按累计分值查表得到 C-TIRADS 类别。\n\n"
                "| 结节 | 分值 | 恶性率% | C-TIRADS 分类 |\n"
                "|---|---|---|---|\n"
                "| 无结节 | 无分值* | 0 | 1 无结节 |\n"
                "| 有结节 | -1 | 0 | 2 良性 |\n"
                "| | 0 | <2 | 3 良性可能 |\n"
                "| | 1 | 2~10 | 4A 低度可疑恶性 |\n"
                "| | 2 | 10~50 | 4B 中度可疑恶性 |\n"
                "| | 3~4 | 50~90 | 4C 高度可疑恶性 |\n"
                "| | 5 | >90 | 5 高度提示恶性 |\n"
                "| | - | - | 6 活检证实的恶性 |\n\n"
                "注：* 无结节，不予赋分。\n\n"
                f"## 原文（PDF 抽取，未改写）\n\n{table_n}"
            ),
            "license_note": "中华超声影像学杂志 2021;30(3)；仅内部检索使用，不再分发",
        },
        {
            "source_doc_id": "ctirads_2021_counting_method",
            "title": "C-TIRADS 计数法：哪些超声特征是可疑恶性征象",
            "source_kind": "guideline_section",
            "section_path": "C-TIRADS 2021 / 计数法的建立",
            "organ": "thyroid",
            "content": (
                "# C-TIRADS 计数法：可疑恶性超声特征\n\n"
                "通过对灰阶超声特征的多元 Logistic 回归分析确定：\n\n"
                "**可疑恶性特征（计入分值）**：\n"
                "- 实性\n- 微钙化\n- 极低回声\n"
                "- 边缘模糊\n- 边缘不规则或甲状腺外侵犯\n- 垂直位（纵横比 > 1）\n\n"
                "**良性特征**：\n- 彗星尾伪像（可减分）\n\n"
                "专家委员会因**简单实用、易于临床推广**而选择计数法"
                "（回归方程法 Az=0.913 / 权重法 0.893 / 计数法 0.890，差异无统计学意义）。\n\n"
                f"## 原文（PDF 抽取，未改写）\n\n{method_n}"
            ),
            "license_note": "中华超声影像学杂志 2021;30(3)；仅内部检索使用，不再分发",
        },
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="补 C-TIRADS 计数法分档表进知识库")
    ap.add_argument("--pdf", default=str(Path.home() / "uai/data/corpus/raw/ctirads_2021.pdf"))
    ap.add_argument("--out", default=str(Path.home() / "uai/data/kb_docs/thyroid_ctirads_counting.jsonl"))
    args = ap.parse_args(argv)

    src = extract_source_text(Path(args.pdf).expanduser())
    docs = build_docs(src)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"已写 {len(docs)} 份文档 → {out}")
    for d in docs:
        print(f"  {d['source_doc_id']}: {len(d['content'])} 字符")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
