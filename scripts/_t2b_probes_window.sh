#!/bin/bash
set -u
UAI="$HOME/uai"
SRC="$UAI/src"

echo "=== 1. 停判官腾显存 ==="
pkill -9 -f 'VLLM::EngineCore' 2>/dev/null
pkill -9 -f 'vllm.entrypoints.openai.api_server' 2>/dev/null
sleep 5
nvidia-smi --query-gpu=memory.used --format=csv,noheader

echo "=== 2. 导出 T2b 探针 ==="
cd "$SRC" && export PYTHONPATH="$SRC"
CUDA_VISIBLE_DEVICES=0 "$UAI/venv/bin/python" scripts/t2b_export_probes.py 2>&1 | tail -5

echo "=== 3. 恢复双 vLLM ==="
nohup bash "$SRC/scripts/_deploy_dual_vllm.sh" > "$UAI/logs/dual_vllm_start.log" 2>&1 &
echo "恢复中"
