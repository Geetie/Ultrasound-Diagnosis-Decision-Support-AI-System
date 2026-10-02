#!/bin/bash
# faithfulness A/B：golden v1（对照）vs v2（+83 新指南文档）
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
PY="$UAI/venv/bin/python"
cd "$SRC"
export PYTHONPATH="$SRC"

echo "=== A. golden v1（433，对照）==="
GOLDEN_PATH="$SRC/data/golden/kb_qa_golden_v1.json" \
OUT_PATH="$SRC/reports/t1_faith_v1_rerun.json" \
N=40 \
"$PY" scripts/server_faith_eval_async.py 2>&1 | tail -3

echo "=== B. golden v2（516，+83 新指南）==="
GOLDEN_PATH="$SRC/data/golden/kb_qa_golden_v2.json" \
OUT_PATH="$SRC/reports/t1_faith_v2_expanded.json" \
N=40 \
"$PY" scripts/server_faith_eval_async.py 2>&1 | tail -3

echo "=== 汇总 ==="
python3 - <<'EOF'
import json
for tag, p in (("v1(433) ", '/mnt/ee16/home/student03/uai/src/reports/t1_faith_v1_rerun.json'),
               ("v2(516) ", '/mnt/ee16/home/student03/uai/src/reports/t1_faith_v2_expanded.json')):
    r = json.load(open(p))
    print(f"{tag} scored={r['scored']} failed={r['failed']} "
          f"official={r['mean_faithfulness']:.4f} lenient={r['mean_faithfulness_lenient']:.4f} "
          f"gen_ms={r['gen_latency_ms_mean']} {r['total_latency_s']}s")
EOF
