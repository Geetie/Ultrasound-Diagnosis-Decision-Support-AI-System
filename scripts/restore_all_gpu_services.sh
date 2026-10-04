#!/usr/bin/env bash
# 恢复全部 GPU 服务（严格串行 —— skill 铁律：双实例并发启动会互踩 memory probe）
# 顺序：判官 8006（GGUF，显存小）→ 主生成 8002（7B 官方 OpenAI server）
set -u
UAI="$HOME/uai"
say() { echo "[$(date +%H:%M:%S)] $*"; }

wait_http() {  # $1=port $2=期望码 $3=超时秒
  local port=$1 want=${2:-200} tmax=${3:-180} i=0
  while [ $i -lt $tmax ]; do
    sleep 3; i=$((i+3))
    c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://127.0.0.1:${port}/health" 2>/dev/null)
    [ "$c" = "$want" ] && { say "  ${port} OK ($((i))s)"; return 0; }
  done
  say "  ${port} TIMEOUT"
  return 1
}

say "=== 1) 判官 8006（先起，显存小）==="
cd "$UAI/src" || exit 1
bash scripts/server_start_judge.sh 2>&1 | tail -2
wait_http 8006 200 120

say "  显存: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"

say "=== 2) 主生成 8002（官方 OpenAI server，7B）==="
export CUDA_VISIBLE_DEVICES=0
export PATH="$UAI/venv-vllm/bin:$PATH"
export VLLM_USE_FLASHINFER_SAMPLER=0
cd "$UAI/src" || exit 1
setsid nohup "$UAI/venv-vllm/bin/python" -m vllm.entrypoints.openai.api_server \
  --model "$UAI/models/Qwen2.5-7B-Instruct" --served-model-name Qwen2.5-7B-Instruct \
  --host 127.0.0.1 --port 8002 --gpu-memory-utilization 0.46 \
  --max-num-batched-tokens 16384 > "$UAI/logs/vllm.log" 2>&1 < /dev/null &
disown
wait_http 8002 200 240

say "=== 3) 验 8002 标准端点 + chat ==="
curl -s --max-time 20 http://127.0.0.1:8002/v1/models | head -c 150; echo ""
curl -s --max-time 90 -X POST http://127.0.0.1:8002/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen2.5-7B-Instruct","messages":[{"role":"user","content":"说OK"}],"max_tokens":8}' \
  | head -c 200; echo ""

say "=== 4) 全端口 ==="
for p in 8002 8003 8006 9001 18000; do
  ep=/health; [ "$p" = "18000" ] && ep=/api/v1/health
  printf "  %-6s %s\n" "$p" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "http://127.0.0.1:${p}${ep}" 2>/dev/null || echo down)"
done
say "  显存: $(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
