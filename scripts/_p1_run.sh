#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
cd "$UAI/src"
nohup env PYTHONPATH="$UAI/src" "$UAI/venv/bin/python" scripts/consistency_verify.py \
  --cases app/inference/data/consistency_test_cases.json \
  --vllm-model 'Qwen2.5-7B-Instruct' \
  --concurrency 4 --timeout 120 --use-chat --guided-json \
  --output "$UAI/reports/consistency_780_guided_v2_report.json" \
  > "$UAI/logs/consistency_guided_v2.log" 2>&1 &
echo "guided-v2-started"
