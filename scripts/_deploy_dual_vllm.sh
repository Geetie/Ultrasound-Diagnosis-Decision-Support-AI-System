#!/usr/bin/env bash
# 双 vLLM 串行启动：主（0.46）就绪 → 判官 Qwen3-8B（0.36）
# 显存账：基线 6.8（嵌入x3）+ 主 21.6 + judge 16.9 ≈ 45.3 / 47.4
set -u
UAI="$HOME/uai"
export CUDA_VISIBLE_DEVICES=0
export VLLM_USE_FLASHINFER_SAMPLER=0
VENV="$UAI/venv-vllm/bin"

nohup "$VENV/python" -m vllm.entrypoints.openai.api_server \
  --model "$UAI/models/Qwen2.5-7B-Instruct" \
  --served-model-name Qwen2.5-7B-Instruct \
  --host 127.0.0.1 --port 8002 \
  --gpu-memory-utilization 0.46 \
  --max-num-batched-tokens 16384 \
  > "$UAI/logs/vllm.log" 2>&1 &

# 等主就绪（最多 300s）
for i in $(seq 1 60); do
  curl -s --max-time 3 http://127.0.0.1:8002/v1/models | grep -q Qwen2.5 && break
  sleep 5
done
curl -s --max-time 3 http://127.0.0.1:8002/v1/models | grep -q Qwen2.5 \
  || { echo "main-8002-NOT-READY"; exit 1; }
echo "main-8002-ready"

nohup "$VENV/python" -m vllm.entrypoints.openai.api_server \
  --model "$UAI/models/Qwen3-8B" \
  --served-model-name Qwen3-8B \
  --host 127.0.0.1 --port 8006 \
  --gpu-memory-utilization 0.36 \
  --max-model-len 4096 \
  --max-num-seqs 16 \
  > "$UAI/logs/judge_8006.log" 2>&1 &

for i in $(seq 1 40); do
  curl -s --max-time 3 http://127.0.0.1:8006/v1/models | grep -q Qwen3 && break
  sleep 5
done
curl -s --max-time 3 http://127.0.0.1:8006/v1/models | grep -q Qwen3 \
  || { echo "judge-8006-NOT-READY"; exit 1; }
echo "judge-8006-ready"
