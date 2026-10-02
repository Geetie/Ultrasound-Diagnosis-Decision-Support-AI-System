#!/bin/bash
# 0A 重测方案 C：回到 Phase0A 原始模型口径 Qwen2.5-1.5B
# llama.cpp CPU（1.5B Q4 够快） vs vLLM 新实例 8007（1.5B bf16，不占生产主服务）
# 全程不干扰 8002/8003；结束恢复判官 8006
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
LLAMA_DIR="$UAI/models/llama-b11272"

echo "=== A. 清理残留 ==="
for pat in 'consistency_verify.py' '_p0a_retest' 'llama-server'; do
  for pid in $(pgrep -f "$pat"); do
    [ "$pid" != "$$" ] && kill -9 "$pid" 2>/dev/null && echo "killed $pid ($pat)"
  done
done
sleep 2

echo "=== B. llama-server CPU 1.5B Q4（端口 8001）==="
nohup "$LLAMA_DIR/llama-server" \
  -m "$UAI/models/qwen2.5-1.5b-instruct-gguf/qwen2.5-1.5b-instruct-q4_k_m.gguf" \
  --host 127.0.0.1 --port 8001 -c 4096 -t 16 --parallel 4 \
  > "$UAI/logs/llama_server_8001.log" 2>&1 &
for i in $(seq 1 24); do
  curl -s --max-time 3 http://127.0.0.1:8001/health | grep -q ok && break
  sleep 5
done
curl -s --max-time 3 http://127.0.0.1:8001/health || { echo "llama NOT READY"; exit 1; }
echo "llama-server ready (CPU 1.5B)"

echo "=== C. vLLM 1.5B 新实例（端口 8007）==="
export CUDA_VISIBLE_DEVICES=0
export VLLM_USE_FLASHINFER_SAMPLER=0
nohup "$UAI/venv-vllm/bin/python" -m vllm.entrypoints.openai.api_server \
  --model "$UAI/models/Qwen2.5-1.5B-Instruct" \
  --served-model-name Qwen2.5-1.5B-Instruct \
  --host 127.0.0.1 --port 8007 \
  --gpu-memory-utilization 0.12 \
  --max-model-len 4096 \
  > "$UAI/logs/vllm_8007.log" 2>&1 &
for i in $(seq 1 40); do
  curl -s --max-time 3 http://127.0.0.1:8007/v1/models | grep -q Qwen2.5 && break
  sleep 5
done
curl -s --max-time 3 http://127.0.0.1:8007/v1/models | grep -q Qwen2.5 || { echo "vllm8007 NOT READY"; exit 1; }
echo "vllm 8007 ready"

echo "=== D. 双引擎一致性验证（780 例，1.5B 对）==="
cd "$SRC"
export PYTHONPATH="$SRC"
"$UAI/venv/bin/python" scripts/consistency_verify.py \
  --cases "$SRC/data/consistency_test_cases.json" \
  --llama-cpp-host 127.0.0.1 --llama-cpp-port 8001 \
  --vllm-host 127.0.0.1 --vllm-port 8007 --vllm-model Qwen2.5-1.5B-Instruct \
  --use-chat --guided-json --concurrency 8 --timeout 60 \
  --output "$SRC/reports/phase0a_retest.json" 2>&1 | tail -25

echo "=== E. 清理临时实例，恢复判官 8006 ==="
pkill -9 -f 'llama-server' 2>/dev/null
for pid in $(pgrep -f 'port 8007'); do kill -9 "$pid" 2>/dev/null; done
sleep 3
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
