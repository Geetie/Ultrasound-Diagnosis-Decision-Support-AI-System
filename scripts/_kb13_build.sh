#!/bin/bash
# KB v1.3.0 建库：kb_v1.2.0 语料 + 3 份新指南 PDF（增量并入 source-dir）
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
V12="$SRC/data/kb_src_v12"
V13="$SRC/data/kb_src_v13"
RAW="$UAI/data/corpus/raw"
PY="$UAI/venv/bin/python"

set -e
echo "=== 1. 复制 v12 → v13 ==="
rm -rf "$V13"
cp -r "$V12" "$V13"
ls "$V13"

echo "=== 2. 新 PDF 按器官分拣 ==="
rm -rf /tmp/pdf_new_thyroid /tmp/pdf_new_liver
mkdir -p /tmp/pdf_new_thyroid /tmp/pdf_new_liver
cp "$RAW/thyroid_cancer_guideline_2022.pdf" "$RAW/ctirads_2021.pdf" /tmp/pdf_new_thyroid/
cp "$RAW/liver_us_diagnosis_guideline_2021.pdf" /tmp/pdf_new_liver/

echo "=== 3. 解析并入 source-dir ==="
cd "$SRC"
PYTHONPATH="$SRC" "$PY" scripts/build_pdf_corpus.py \
  --pdf-dir /tmp/pdf_new_thyroid --organ thyroid \
  --emit-source-dir --out "$V13"
PYTHONPATH="$SRC" "$PY" scripts/build_pdf_corpus.py \
  --pdf-dir /tmp/pdf_new_liver --organ liver \
  --emit-source-dir --out "$V13"

echo "=== 4. manifest 修 kb_version ==="
python3 - "$V13" <<'EOF'
import json, sys, pathlib
root = pathlib.Path(sys.argv[1])
m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
m["kb_version"] = "kb_v1.3.0"
organs = sorted(d.name for d in root.iterdir() if d.is_dir())
m["organs"] = organs
(root / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
for o in organs:
    n = len(list((root / o).glob("*.json")))
    print(o, n, "docs")
EOF

echo "=== 5. init_knowledge_base ==="
PYTHONPATH="$SRC" "$PY" scripts/init_knowledge_base.py \
  --kb-version kb_v1.3.0 --source-dir "$V13"

echo "=== 6. 向量灌数 ==="
PYTHONPATH="$SRC" "$PY" scripts/server_populate_vector_index.py \
  --kb-version kb_v1.3.0 --purge

echo "=== DONE kb_v1.3.0 ==="
