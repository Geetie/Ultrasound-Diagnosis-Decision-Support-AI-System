#!/usr/bin/env bash
# 恢复 8006（Qwen3-8B，OpenAI 兼容 api_server）
set -u
export CUDA_VISIBLE_DEVICES=0
export PATH="$HOME/uai/venv-vllm/bin:$PATH"
export VLLM_USE_FLASHINFER_SAMPLER=0
MODEL="$HOME/uai/models/Qwen3-8B"
echo "model dir: $(ls -d "$MODEL" 2>/dev/null || echo MISSING)"
mkdir -p ~/uai/logs
cd ~/uai/src || exit 1
setsid nohup ~/uai/venv-vllm/bin/python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" --served-model-name Qwen3-8B --port 8006 \
  --max-model-len 4096 --gpu-memory-utilization 0.40 --max-num-seqs 16 \
  >> ~/uai/logs/vllm8006.log 2>&1 < /dev/null &
disown
for i in $(seq 1 80); do
  sleep 5
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8006/health 2>/dev/null)
  if [ "$c" = "200" ]; then echo "VLLM_8006_OK after $((i*5))s"; break; fi
  if [ $((i % 6)) -eq 0 ]; then
    echo "  ... $((i*5))s mem=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
  fi
done
echo "=== 结果 ==="
for p in 8002 8006; do
  printf "  %s -> %s\n" "$p" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:$p/health 2>/dev/null || echo down)"
done
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
