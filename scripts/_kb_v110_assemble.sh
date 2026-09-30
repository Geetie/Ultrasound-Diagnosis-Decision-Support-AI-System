#!/usr/bin/env bash
# KB v1.1.0 组装：thyroid（沿用 v1.0.0）+ liver/cardiac（新对话语料）
set -u
UAI="$HOME/uai"
SRC="$UAI/data/kb_sources/kb_v1.1.0"

# 1) 上传的两个 jsonl 转成 per-doc json
PY="$UAI/venv/bin/python"
"$PY" - "$UAI/data/kb_docs" <<'EOF'
import json
import sys
from pathlib import Path

src = Path(sys.argv[1])
for organ in ("liver", "cardiac"):
    out_dir = Path.home() / f"uai/data/kb_sources/kb_v1.1.0/{organ}"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for line in (src / f"{organ}_docs.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        (out_dir / f"{d['source_doc_id']}.json").write_text(
            json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        n += 1
    print(organ, n, "docs")
EOF

# 2) thyroid 沿用 v1.0.0 文档
mkdir -p "$SRC/thyroid"
cp -n "$UAI/data/kb_sources/kb_v1.0.0/thyroid/"*.json "$SRC/thyroid/" 2>/dev/null || true
echo "thyroid: $(ls "$SRC/thyroid" | wc -l) docs"

# 3) manifest
echo '{"kb_version": "kb_v1.1.0", "embedding_model": "bge-m3-medical"}' > "$SRC/manifest.json"

# 4) 建库（真实 bge-m3 嵌入）
cd "$UAI/src"
PYTHONPATH="$UAI/src" "$PY" scripts/init_knowledge_base.py \
  --kb-version kb_v1.1.0 --source-dir "$SRC" --force-rebuild 2>&1 | grep -viE 'jieba|warning' | tail -4

# 5) 灌向量索引（三器官，--purge 清旧）
PYTHONPATH="$UAI/src" "$PY" scripts/server_populate_vector_index.py \
  --kb-version kb_v1.1.0 --purge 2>&1 | tail -2
