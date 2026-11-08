#!/usr/bin/env bash
# 第 4 组：4.2 幻觉 + 4.3 faithfulness 后台跑（含判官，约 20-40 分钟）
set -u
UAI="$HOME/uai"
cd "$UAI/src" || exit 1
exec "$UAI/venv/bin/python" scripts/audit/run_with_env.py \
  scripts/audit/p108_hallucination_faithfulness.py \
  --report "$UAI/reports/p108_hf_final.json"
