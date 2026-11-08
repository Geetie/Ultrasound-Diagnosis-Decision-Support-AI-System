#!/usr/bin/env bash
# 临时启动 8002 vLLM（II-Medical-8B）—— 第 4 组生成质量评测用。
# 与 scripts/window_8002_restart.sh 同款三环境变量，仅换模型与 served-name。
set -u
UAI="$HOME/uai"
MODEL="$UAI/models/II-Medical-8B"
export CUDA_VISIBLE_DEVICES=0
export PATH="$UAI/venv-vllm/bin:$PATH"
export VLLM_USE_FLASHINFER_SAMPLER=0

if [ ! -d "$MODEL" ]; then echo "MODEL missing: $MODEL"; exit 1; fi

echo "GPU before: $(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
cd "$UAI/src" || exit 1
nohup "$UAI/venv-vllm/bin/python" -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" --served-model-name II-Medical-8B \
  --host 127.0.0.1 --port 8002 --gpu-memory-utilization 0.42 \
  --max-num-batched-tokens 16384 --max-model-len 8192 \
  > "$UAI/logs/vllm_8002_g4.log" 2>&1 &
echo "started pid $!"

for i in $(seq 1 60); do
  sleep 5
  if curl -s --max-time 3 http://127.0.0.1:8002/health >/dev/null 2>&1; then
    echo "8002-ready (waited $((i*5))s)"
    echo "models: $(curl -s --max-time 5 http://127.0.0.1:8002/v1/models | head -c 300)"
    echo "GPU after: $(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
    exit 0
  fi
done
echo "8002-NOT-READY"
tail -20 "$UAI/logs/vllm_8002_g4.log" | tr -d '\000'
exit 2
