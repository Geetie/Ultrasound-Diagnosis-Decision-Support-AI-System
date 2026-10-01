#!/usr/bin/env bash
# kb_v1.2.0 建库三步：init（chunk+入 knowledge_chunks）→ populate（向量索引）
set -u
UAI="$HOME/uai"
cd "$UAI/src" || exit 1
set -a
source "$UAI/.env"
set +a
export PYTHONPATH="$UAI/src"

echo "=== init_knowledge_base"
"$UAI/venv/bin/python" scripts/init_knowledge_base.py \
  --kb-version kb_v1.2.0 \
  --source-dir "$UAI/src/data/kb_src_v12" \
  --organs thyroid || exit 1

echo "=== populate_vector_index"
"$UAI/venv/bin/python" scripts/server_populate_vector_index.py \
  --kb-version kb_v1.2.0 || exit 1

echo "=== done"
