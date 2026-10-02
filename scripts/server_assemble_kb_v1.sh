#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
export PYTHONPATH="$UAI/src"
rm -rf "$UAI/data/kb_sources/kb_v1.0.0"
mkdir -p "$UAI/data/kb_sources/kb_v1.0.0/thyroid"

"$UAI/venv/bin/python" - <<'EOF'
import json
import re
from pathlib import Path
from pypdf import PdfReader

out_dir = Path.home() / "uai/data/kb_sources/kb_v1.0.0/thyroid"
out_dir.mkdir(parents=True, exist_ok=True)

pdf = Path.home() / "uai/data/corpus/raw/thyroid_cancer_norm_2018.pdf"
reader = PdfReader(str(pdf))
text = "\n".join((p.extract_text() or "") for p in reader.pages)
# 清洗：medlive 水印行、独立页码行
text = re.sub(r'http://guide\.medlive\.cn/\s*', '', text)
text = re.sub(r'\n\s*\d+\s*\n', '\n', text)
print("cleaned chars:", len(text))

# 按中文序号章节切分：一、二、…十、十一…
marks = list(re.finditer(r'\n\s*([一二三四五六七八九十]{1,2})、\s*', text))
print("section marks:", len(marks), [m.group(1) for m in marks][:15])
bounds = [m.start() for m in marks] + [len(text)]
sections = [text[bounds[i]:bounds[i + 1]].strip() for i in range(len(marks))]

count = 0
for i, sec in enumerate(sections):
    if len(sec) < 300:
        continue
    title = sec.splitlines()[0][:50].strip()
    doc = {
        "source_doc_id": f"norm2018_s{i:02d}",
        "title": f"甲状腺癌诊疗规范(2018) {title}",
        "source_kind": "guidelines",
        "section_path": title,
        "content": sec,
    }
    (out_dir / f"{doc['source_doc_id']}.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    count += 1
print("norm section docs:", count)

# 对话文档并入
n = 0
src = Path.home() / "uai/data/kb_sources/dialogue_docs.jsonl"
for line in src.open(encoding="utf-8"):
    d = json.loads(line)
    (out_dir / f"{d['source_doc_id']}.json").write_text(
        json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    n += 1
print("dialogue docs:", n)

# manifest
manifest = {"kb_version": "kb_v1.0.0", "embedding_model": "bge-m3-medical"}
(Path.home() / "uai/data/kb_sources/kb_v1.0.0/manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
)
total = len(list(out_dir.glob("*.json")))
print("TOTAL docs:", total)
EOF
