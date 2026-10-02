#!/bin/bash
# Gate 0A-Tech-P0 双引擎一致性重测（llama.cpp b11272 vs vLLM 8002，同模型 Qwen2.5-7B）
# 判官 8006 临时停机腾显存，测完自动恢复
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
LS="$UAI/models/llama-b11272/llama-server"
GGUF="$UAI/models/qwen2.5-7b-instruct-gguf/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"

echo "=== 1. 停判官 8006（腾 ~17GB）==="
pkill -f 'port 8006' 2>/dev/null
sleep 5
nvidia-smi --query-gpu=memory.used --format=csv,noheader

echo "=== 2. 起 llama-server 8001（Qwen2.5-7B Q4_K_M，全层 offload）==="
nohup "$LS" -m "$GGUF" --host 127.0.0.1 --port 8001 \
  -ngl 999 -c 4096 -t 8 > "$UAI/logs/llama_server_8001.log" 2>&1 &
for i in $(seq 1 40); do
  curl -s --max-time 3 http://127.0.0.1:8001/health | grep -q true && break
  sleep 5
done
curl -s --max-time 3 http://127.0.0.1:8001/health || { echo "llama-server NOT READY"; exit 1; }
echo "llama-server ready"

echo "=== 3. 生成 ≥700 例分层用例 ==="
cd "$SRC"
export PYTHONPATH="$SRC"
"$UAI/venv/bin/python" scripts/server_build_eval_cases.py \
  --output "$SRC/data/consistency_test_cases.json" 2>&1 | tail -5

echo "=== 4. 双引擎一致性验证（guided json，并发 8）==="
"$UAI/venv/bin/python" scripts/consistency_verify.py \
  --cases "$SRC/data/consistency_test_cases.json" \
  --llama-cpp-host 127.0.0.1 --llama-cpp-port 8001 \
  --vllm-host 127.0.0.1 --vllm-port 8002 --vllm-model Qwen2.5-7B-Instruct \
  --use-chat --guided-json --concurrency 8 --timeout 60 \
  --output "$SRC/reports/phase0a_retest.json" 2>&1 | tail -20

echo "=== 5. 停 llama-server，恢复判官 8006 ==="
pkill -f 'llama-server' 2>/dev/null
sleep 3
VENV_VLLM="$UAI/venv-vllm/bin"
export CUDA_VISIBLE_DEVICES=0
export VLLM_USE_FLASHINFER_SAMPLER=0
nohup "$VENV_VLLM/python" -m vllm.entrypoints.openai.api_server \
  --model "$UAI/models/Qwen3-8B" \
  --served-model-name Qwen3-8B \
  --host 127.0.0.1 --port 8006 \
  --gpu-memory-utilization 0.36 \
  --max-model-len 4096 \
  --max-num-seqs 16 \
  > "$UAI/logs/judge_8006.log" 2>&1 &
echo "判官恢复中（judge_8006.log）"
echo "=== DONE ==="
