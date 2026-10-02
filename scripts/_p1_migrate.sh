#!/usr/bin/env bash
# P1 效率专项：llama-cpp-python 包装 → 官方 llama-server（continuous batching + 原生 GBNF）
set -u
UAI="$HOME/uai"
SERVER="$UAI/models/llama-b11272/llama-server"
GGUF="$UAI/models/qwen2.5-7b-instruct-gguf/qwen2.5-7b-instruct-q8_0-00001-of-00003.gguf"
export CUDA_VISIBLE_DEVICES=0
export LD_LIBRARY_PATH="$UAI/models/llama-b11272:${LD_LIBRARY_PATH:-}"

echo "=== 版本 ==="
"$SERVER" --version 2>&1 | head -2

echo "=== 停旧包装服务 ==="
pkill -f llama_cpp_server.py 2>/dev/null || true
sleep 2

echo "=== 启动 llama-server（--parallel 4 连续批处理）==="
cd "$UAI/src"
nohup "$SERVER" \
  --model "$GGUF" \
  --host 127.0.0.1 --port 8001 \
  --ctx-size 8192 \
  --n-gpu-layers 99 \
  --parallel 4 \
  --threads-http 4 \
  >> "$UAI/logs/llama_server.log" 2>&1 &
for i in $(seq 1 120); do
  sleep 2
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8001/health 2>/dev/null || true)
  [ "$code" = "200" ] && { echo "8001 ready (t≈$((i*2))s)"; break; }
  if ! pgrep -f 'llama-server' > /dev/null; then
    echo "!! llama-server died:"; tail -8 "$UAI/logs/llama_server.log" | tr -d '\000'; exit 2
  fi
done

echo "=== 冒烟：chat ==="
curl -s --max-time 90 -X POST http://127.0.0.1:8001/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"甲状腺结节，请给出TI-RADS分级。"}],"max_tokens":64,"temperature":0}' \
  | head -c 200
echo
echo "=== 冒烟：grammar（P0-2 同款，官方实现）==="
curl -s --max-time 90 -X POST http://127.0.0.1:8001/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"甲状腺结节，请给出TI-RADS分级。"}],"max_tokens":48,"temperature":0,"grammar":"root ::= \"{\" ws \"\\\"grading\\\"\" ws \":\" ws grading-value ws \"}\" ws\ngrading-value ::= \"\\\"\" ( \"2\" | \"3\" | \"4a\" | \"4b\" | \"5\" ) \"\\\"\"\nws ::= \" \"*"}' \
  | "$UAI/venv/bin/python" -c "import json,sys; print('grammar-out:', json.load(sys.stdin)['choices'][0]['message']['content'][:80])" 2>&1
echo "=== 进程存活 ==="
pgrep -c -f llama-server
