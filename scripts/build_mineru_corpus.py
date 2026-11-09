"""从 MinerU 输出的 Markdown 构建 KB 源文档目录（kb_v2.0.0 摄取层）。

## 与旧 ``build_pdf_corpus.py`` 的差别

| | build_pdf_corpus（旧） | 本脚本（新） |
|---|---|---|
| 解析 | pymupdf4llm | **MinerU 4.0.12**（`--tier flash`） |
| 表格 | `<br>` 切断数值、表头丢失 | 围栏代码块内逐行对齐的完整表 |
| 切节 | 按 `#` 正则，丢掉标题层级 | 保留 `#/##/###` 全层级 |
| 元数据 | 只有 organ/section_path | 加 source_set / source_authority / publish_year |

**不在这里切 chunk**——只负责"PDF/Markdown → 结构化源文档 JSON"，
切分由 ``app.knowledge.md_chunker`` 做（结构感知 + 表格保护 + 父子块）。

## 用法（服务器）

    PYTHONPATH=$HOME/uai/src ~/uai/venv/bin/python scripts/build_mineru_corpus.py \\
        --md-dir ~/uai/data/corpus/mineru_md \\
        --out ~/uai/data/corpus/kb_v2_src --kb-version kb_v2.0.0

产出 ``<out>/manifest.json`` + ``<out>/<organ>/*.json``，可直接喂
``scripts/init_knowledge_base.py --source-dir``。

## 器官归属

MinerU 输出文件名不带器官，本脚本按**文件名规则**映射（见 ``_ORGAN_RULES``）。
映射不上直接 FAIL —— 不猜（猜错会把心脏指南塞进甲状腺库）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 文件名前缀 → 器官。顺序敏感，先匹配者胜。
_ORGAN_RULES: tuple[tuple[str, str], ...] = (
    ("ctirads", "thyroid"),
    ("thyroid_cancer", "thyroid"),
    ("ata_", "thyroid"),
    ("acr_ceus_lirads", "liver"),
    ("acr_ceus_tra", "liver"),
    ("acr_lirads_us", "liver"),
    ("liver_", "liver"),
    ("ase_", "cardiac"),
    ("bse_", "cardiac"),
    ("cardiac_", "cardiac"),
    ("jaha_", "cardiac"),
)

#: MinerU 图片占位：``![Image block](doc:…)`` —— 保留会把假引用灌进 KB
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(doc:[^)]*\)")
#: 公式块占位同样指向 doc: 伪路径
_FORMULA_RE = re.compile(r"!\[Formula block\]\(doc:[^)]*\)")
#: 页脚小字（DOI/通信作者），对检索无价值且干扰
_FOOTNOTE_RE = re.compile(
    r'<small><span class="docvortex-page-footnote".*?</span></small>',
    re.DOTALL,
)

MIN_DOC_CHARS = 60


def organ_for(stem: str) -> str | None:
    low = stem.lower()
    for prefix, organ in _ORGAN_RULES:
        if low.startswith(prefix):
            return organ
    return None


def clean_markdown(md: str) -> str:
    """清掉 MinerU 的图片/公式伪引用与页脚，保留标题与表格。"""
    md = _FOOTNOTE_RE.sub("", md)
    md = _IMAGE_RE.sub("", md)
    md = _FORMULA_RE.sub("", md)
    # 折叠 3+ 空行
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def split_by_top_heading(md: str, fallback: str) -> list[tuple[str, str]]:
    """按 ``#``/``##`` 切节，返回 [(section_path, body)]。

    与旧脚本不同：**不丢弃**标题层级，section_path 用 ``A / B`` 形式，
    让下游 chunk 能带上"表1 在 C-TIRADS 第几节"这种可追溯结构。
    """
    lines = md.split("\n")
    sections: list[tuple[str, str]] = []
    path: list[tuple[int, str]] = []
    body: list[str] = []

    def flush() -> None:
        nonlocal body
        text = "\n".join(body).strip()
        if text:
            sections.append((" / ".join(t for _, t in path) or fallback, text))
        body = []

    for line in lines:
        m = re.match(r"^(#{1,3})\s+(.*\S)\s*$", line)
        if m:
            flush()
            level = len(m.group(1))
            path = [(lv, t) for lv, t in path if lv < level]
            path.append((level, m.group(2).strip()))
            continue
        body.append(line)
    flush()
    return sections


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--md-dir", required=True, help="MinerU markdown 输出目录")
    ap.add_argument("--out", required=True, help="源文档目录（含 manifest.json）")
    ap.add_argument("--kb-version", default="kb_v2.0.0")
    ap.add_argument("--min-chars", type=int, default=MIN_DOC_CHARS)
    args = ap.parse_args(argv)

    md_dir = Path(args.md_dir).expanduser()
    out_root = Path(args.out).expanduser()
    if not md_dir.is_dir():
        print(f"FATAL: md-dir not found: {md_dir}", flush=True)
        return 2

    mds = sorted(md_dir.glob("*.md"))
    if not mds:
        print(f"FATAL: no .md under {md_dir}", flush=True)
        return 2

    per_organ: dict[str, list[dict]] = {}
    stats: list[str] = []
    skipped: list[str] = []

    for md_path in mds:
        stem = md_path.stem
        organ = organ_for(stem)
        if organ is None:
            skipped.append(f"{stem}: 器官映射不上（_ORGAN_RULES 无匹配）")
            continue
        raw = md_path.read_text(encoding="utf-8")
        md = clean_markdown(raw)
        if len(md) < args.min_chars:
            skipped.append(f"{stem}: 正文仅 {len(md)} 字符 (<{args.min_chars})")
            continue

        docs = per_organ.setdefault(organ, [])
        seq = 0
        for section_path, body in split_by_top_heading(md, fallback=stem):
            if len(body) < args.min_chars:
                continue
            docs.append({
                "source_doc_id": f"pdf_{organ}_{stem}_{seq:04d}",
                "title": section_path[:120],
                "source_kind": "guidelines",
                "section_path": f"{stem} / {section_path}",
                "content": body,
            })
            seq += 1
        stats.append(f"{stem:44s} -> {organ:8s} {seq:3d} docs, {len(md):7d} chars")

    if not per_organ:
        print("FATAL: 没有任何文档被产出", flush=True)
        return 3

    out_root.mkdir(parents=True, exist_ok=True)
    total = 0
    for organ, docs in per_organ.items():
        organ_dir = out_root / organ
        organ_dir.mkdir(parents=True, exist_ok=True)
        for d in docs:
            (organ_dir / f"{d['source_doc_id']}.json").write_text(
                json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        total += len(docs)

    manifest = {
        "kb_version": args.kb_version,
        "source": f"mineru_md:{md_dir}",
        "parser": "MinerU 4.0.12 (tier=flash)",
        "organs": {k: len(v) for k, v in sorted(per_organ.items())},
        "total_docs": total,
    }
    (out_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n".join(stats), flush=True)
    if skipped:
        print("\n-- SKIPPED --", flush=True)
        print("\n".join(f"  {s}" for s in skipped), flush=True)
    print(
        f"\nDONE: {total} docs / {len(per_organ)} organs -> {out_root}\n"
        f"manifest: {json.dumps(manifest['organs'], ensure_ascii=False)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
