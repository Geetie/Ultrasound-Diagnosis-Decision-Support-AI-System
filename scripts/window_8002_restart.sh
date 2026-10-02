#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
pkill -9 -f "vllm.entrypoints.openai.api_server" 2>/dev/null
pkill -9 -f "VLLM::EngineCore" 2>/dev/null
sleep 3
export CUDA_VISIBLE_DEVICES=0
export PATH="$UAI/venv-vllm/bin:$PATH"
export VLLM_USE_FLASHINFER_SAMPLER=0
cd "$UAI/src"
nohup "$UAI/venv-vllm/bin/python" -m vllm.entrypoints.openai.api_server \
  --model "$UAI/models/Qwen2.5-7B-Instruct" --served-model-name Qwen2.5-7B-Instruct \
  --host 127.0.0.1 --port 8002 --gpu-memory-utilization 0.46 \
  --max-num-batched-tokens 16384 > "$UAI/logs/vllm.log" 2>&1 &
echo "8002-starting"
for i in $(seq 1 40); do
    sleep 5
    if curl -s --max-time 3 http://127.0.0.1:8002/health >/dev/null 2>&1; then
        echo "8002-ready (waited $((i*5))s)"
        nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
        exit 0
    fi
done
echo "8002-NOT-READY"