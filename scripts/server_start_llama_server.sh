#!/usr/bin/env bash
# P1 效率专项（15 号计划）：官方 llama-server 替换 llama-cpp-python 包装。
#
# 为什么迁移（真机 2026-09-30 实测）：
# 1. llama-cpp-python 包装无 continuous batching——单请求串行，780 例
#    一致性评测 1 例/6-26s；llama-server --parallel 4 下并发摊薄 1.8s/例；
# 2. llama-cpp-python 0.3.35 的 grammar 三连坑（转义引号+chat 组合段错误、
#    字面量引号不输出、规则名不含下划线）；llama-server 原生 GBNF 无此问题；
# 3. 客户端契约不变：OpenAI 风格 /v1/chat/completions、/v1/completions、
#    /health，grammar 字段透传（app/inference/llama_cpp.py 已实现）。
#
# 用法：
#   bash scripts/server_start_llama_server.sh [GGUF路径]
#   （默认 Qwen2.5-7B Q8_0 分片；--parallel 4 连续批处理）
set -u
UAI="${UAI:-$HOME/uai}"
SERVER="$UAI/models/llama-b11272/llama-server"
MODEL="${1:-$UAI/models/qwen2.5-7b-instruct-gguf/qwen2.5-7b-instruct-q8_0-00001-of-00003.gguf}"
PORT="${PORT:-8001}"
PARALLEL="${PARALLEL:-4}"
# ⚠️ llama-server 的 ctx-size 是**总池**，--parallel 会切分给各 slot：
# 诊断 prompt 实测 5943+1024 tokens/slot，4 slot × 8192 才够（总 32768）。
# 真机 2026-09-30 实测：8192 总池下每 slot 仅 2048 → prompt 超 400。
CTX="${CTX:-32768}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export LD_LIBRARY_PATH="$UAI/models/llama-b11272:${LD_LIBRARY_PATH:-}"

if [ ! -x "$SERVER" ]; then
  echo "llama-server 不存在：$SERVER（见 16 号 A.8 下载与解压步骤）"
  exit 1
fi

if curl -s -o /dev/null --max-time 3 "http://127.0.0.1:$PORT/health" 2>/dev/null; then
  echo "端口 $PORT 已有服务（幂等跳过）"
  exit 0
fi

pkill -f 'llama_cpp_server.py' 2>/dev/null || true
sleep 2

cd "$UAI/src"
nohup "$SERVER" \
  --model "$MODEL" \
  --host 127.0.0.1 --port "$PORT" \
  --ctx-size "$CTX" \
  --n-gpu-layers 99 \
  --parallel "$PARALLEL" \
  --flash-attn on \
  --threads-http 4 \
  >> "$UAI/logs/llama_server.log" 2>&1 &

for i in $(seq 1 120); do
  sleep 2
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$PORT/health" 2>/dev/null || true)
  [ "$code" = "200" ] && { echo "llama-server ready on :$PORT (t≈$((i*2))s, parallel=$PARALLEL)"; exit 0; }
  if ! pgrep -f llama-server > /dev/null; then
    echo "!! llama-server 退出："; tail -8 "$UAI/logs/llama_server.log" | tr -d '\000'
    exit 1
  fi
done
echo "llama-server 健康探活超时"; exit 1
