#!/bin/bash
# 0A 重测续跑：杀残留 verify → CUDA 姿势重启 llama-server → 重跑 step4 → 恢复判官
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
LLAMA_DIR="$UAI/models/llama-b11272"

echo "=== A. 杀残留 verify 与原编排脚本（按 PID，防自杀）==="
for pat in 'consistency_verify.py' '_p0a_retest.sh'; do
  for pid in $(pgrep -f "$pat"); do
    [ "$pid" != "$$" ] && kill -9 "$pid" 2>/dev/null && echo "killed $pid ($pat)"
  done
done

echo "=== B. CUDA 姿势重启 llama-server ==="
pkill -9 -f 'llama-server' 2>/dev/null
sleep 2
# CUDA 13 运行时来自 vLLM 的 nvidia pip 包（系统只有 CUDA 12.4）
NV13="$(ls -d "$UAI"/venv-vllm/lib/python*/site-packages/nvidia/cu13/lib 2>/dev/null | head -1)"
export LD_LIBRARY_PATH="$LLAMA_DIR:$NV13:$LD_LIBRARY_PATH"
echo "LD_LIBRARY_PATH=$LD_LIBRARY_PATH"
nohup "$LLAMA_DIR/llama-server" \
  -m "$UAI/models/qwen2.5-7b-instruct-gguf/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf" \
  --host 127.0.0.1 --port 8001 -ngl 999 -c 4096 -t 8 \
  > "$UAI/logs/llama_server_8001.log" 2>&1 &
for i in $(seq 1 30); do
  curl -s --max-time 3 http://127.0.0.1:8001/health | grep -q ok && break
  sleep 5
done
curl -s --max-time 3 http://127.0.0.1:8001/health || { echo "NOT READY"; exit 1; }
echo "llama-server ready"
grep -ciE 'cuda' "$UAI/logs/llama_server_8001.log" || true
grep -iE 'offloaded' "$UAI/logs/llama_server_8001.log" | head -3 || true
nvidia-smi --query-gpu=memory.used --format=csv,noheader

echo "=== C. 重跑双引擎验证（780 例）==="
cd "$SRC"
export PYTHONPATH="$SRC"
"$UAI/venv/bin/python" scripts/consistency_verify.py \
  --cases "$SRC/data/consistency_test_cases.json" \
  --llama-cpp-host 127.0.0.1 --llama-cpp-port 8001 \
  --vllm-host 127.0.0.1 --vllm-port 8002 --vllm-model Qwen2.5-7B-Instruct \
  --use-chat --guided-json --concurrency 8 --timeout 60 \
  --output "$SRC/reports/phase0a_retest.json" 2>&1 | tail -25

echo "=== D. 停 llama-server，恢复判官 ==="
pkill -9 -f 'llama-server' 2>/dev/null
sleep 3
export CUDA_VISIBLE_DEVICES=0
export VLLM_USE_FLASHINFER_SAMPLER=0
nohup "$UAI/venv-vllm/bin/python" -m vllm.entrypoints.openai.api_server \
  --model "$UAI/models/Qwen3-8B" \
  --served-model-name Qwen3-8B \
  --host 127.0.0.1 --port 8006 \
  --gpu-memory-utilization 0.36 \
  --max-model-len 4096 \
  --max-num-seqs 16 \
  > "$UAI/logs/judge_8006.log" 2>&1 &
echo "判官恢复中"
echo "=== DONE ==="
