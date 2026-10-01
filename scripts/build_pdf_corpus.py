"""KB v1.2 管线化：PDF 语料 → pymupdf4llm Markdown → 标题分节 → JSONL。

产出与 t1b_build_dialogue_docs.py 同格式的 JSONL（五字段文档），
可直接喂给 scripts/init_knowledge_base.py 建 KB。

用法（服务器）:
    ~/uai/venv/bin/python scripts/build_pdf_corpus.py \
        --pdf-dir ~/uai/data/corpus/raw \
        --organ thyroid \
        --out data/corpus/pdf_docs_thyroid.jsonl

依赖：pymupdf、pymupdf4llm（pip 清华源可装，零模型下载）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

# 单文档正文上限（字符）：超过则按二级标题/段落二次切分
MAX_DOC_CHARS = 2500
MIN_DOC_CHARS = 120


def merge_cjk_spaces(text: str) -> str:
    """合并 PDF 提取引入的 CJK 间伪空格与断行（保留英文/数字边界空格）。"""
    text = re.sub(r"([\u4e00-\u9fff])\n+([\u4e00-\u9fff])", r"\1\2", text)
    text = re.sub(r"([\u4e00-\u9fff]) +([\u4e00-\u9fff])", r"\1\2", text)
    return text


def split_long_section(title: str, body: str, seq_base: int) -> list[tuple[str, str]]:
    """超长节按段落聚合到 MAX_DOC_CHARS 以内。"""
    if len(body) <= MAX_DOC_CHARS:
        return [(title, body)]
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for p in paras:
        if size + len(p) > MAX_DOC_CHARS and buf:
            chunks.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(p)
        size += len(p)
    if buf:
        chunks.append("\n\n".join(buf))
    return [(f"{title}（{i + 1}/{len(chunks)}）", c)
            for i, c in enumerate(chunks)]


def pdf_to_sections(pdf_path: Path) -> list[tuple[str, str]]:
    """PDF → Markdown → 按标题切节，返回 [(section_title, body)]。"""
    import pymupdf4llm as p4l

    md = p4l.to_markdown(str(pdf_path), show_progress=False)
    md = merge_cjk_spaces(md)
    lines = md.splitlines()
    sections: list[tuple[str, str]] = []
    cur_title = pdf_path.stem
    cur_body: list[str] = []
    for line in lines:
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            if cur_body:
                sections.append((cur_title, "\n".join(cur_body).strip()))
            cur_title = m.group(2).strip()[:80] or cur_title
            cur_body = []
        else:
            cur_body.append(line)
    if cur_body:
        sections.append((cur_title, "\n".join(cur_body).strip()))
    return [(t, b) for t, b in sections if b]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", required=True)
    parser.add_argument("--organ", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--source-kind", default="guidelines")
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir).expanduser()
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        print(f"FATAL: no PDF under {pdf_dir}", flush=True)
        return 2

    t0 = time.perf_counter()
    docs: list[dict] = []
    stats: list[str] = []
    for pdf in pdfs:
        stem = pdf.stem
        sections = pdf_to_sections(pdf)
        kept = 0
        seq = 0
        for title, body in sections:
            if len(body) < MIN_DOC_CHARS:
                continue
            for sub_title, sub_body in split_long_section(title, body, seq):
                seq += 1
                docs.append({
                    "source_doc_id": f"pdf_{args.organ}_{stem}_{seq:04d}",
                    "title": sub_title[:80],
                    "source_kind": args.source_kind,
                    "section_path": f"{stem}",
                    "content": sub_body,
                })
                kept += 1
        stats.append(f"{stem}: {len(sections)} 节 → {kept} 文档")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    elapsed = time.perf_counter() - t0
    print(f"[{args.organ}] {len(pdfs)} PDF / {len(docs)} 文档 → {out} "
          f"({elapsed:.1f}s)")
    for s in stats:
        print(" ", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
