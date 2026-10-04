#!/usr/bin/env bash
# 恢复 8006 判官（llama_cpp_server + Qwen3-8B-Q4_K_M.gguf）
# 我之前把 8006 改成了 vllm api_server（偏离 skill 约定且占 20.6GB），现按官方脚本恢复。
set -u
UAI="$HOME/uai"
say() { echo "[$(date +%H:%M:%S)] $*"; }

say "=== 1) 停掉占位的 vLLM 8006 ==="
pids=$(ss -ltnp 2>/dev/null | grep ":8006 " | grep -o 'pid=[0-9]*' | cut -d= -f2)
for p in $pids; do
  cmd=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | cut -c1-80)
  say "  kill $p ($cmd)"
  kill -9 "$p" 2>/dev/null || true
done
# EngineCore 是独立子进程，api_server 死后仍占显存
pkill -9 -f "VLLM::EngineCore" 2>/dev/null || true
sleep 5
say "  显存: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
ss -ltn 2>/dev/null | grep ":8006 " || say "  8006 已停"

say "=== 2) 按官方脚本恢复判官 ==="
cd "$UAI/src" || exit 1
bash scripts/server_start_judge.sh 2>&1 | tail -3

say "=== 3) 验判官 ==="
for i in $(seq 1 40); do
  sleep 3
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8006/health 2>/dev/null)
  [ "$c" = "200" ] && { say "  8006 判官就绪 ($((i*3))s)"; break; }
done
ss -ltnp 2>/dev/null | grep ":8006 " | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1 | while read p; do
  say "  cmd=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | cut -c1-100)"
done
say "  显存: $(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
say "  8002: $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8002/health)"
