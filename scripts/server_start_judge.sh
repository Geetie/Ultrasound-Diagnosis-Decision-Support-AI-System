#!/usr/bin/env bash
# P0-3：Qwen3-8B 判官实例（8006，幂等 + 探活）
# 用途：faithfulness/一致性评测的 LLM-as-judge（与诊断双引擎隔离）
# 注意：Qwen3 默认 thinking 模式——评测 prompt 末尾以 /no_think 关闭
#（Qwen3 官方软开关，GGUF chat template 支持）。
set -u
UAI="$HOME/uai"
MODEL="$UAI/models/qwen3-8b-gguf/Qwen3-8B-Q4_K_M.gguf"
export CUDA_VISIBLE_DEVICES=0

if curl -s -o /dev/null -w '' --max-time 3 http://127.0.0.1:8006/health 2>/dev/null; then
  echo "8006 already running"
  exit 0
fi
if [ ! -f "$MODEL" ]; then
  echo "MODEL missing: $MODEL"
  exit 1
fi
pkill -f 'llama_cpp_server.*8006' 2>/dev/null || true
sleep 2
cd "$UAI/src"
nohup "$UAI/venv/bin/python" -u scripts/llama_cpp_server.py \
  --model "$MODEL" --host 127.0.0.1 --port 8006 \
  --n_ctx 4096 --n_gpu_layers -1 \
  >> "$UAI/logs/judge_qwen3.log" 2>&1 &
for i in $(seq 1 120); do
  sleep 2
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8006/health 2>/dev/null || true)
  [ "$code" = "200" ] && { echo "8006 ready (t≈$((i*2))s)"; exit 0; }
done
echo "8006 failed to start"; tail -5 "$UAI/logs/judge_qwen3.log" | tr -d '\000'
exit 1
