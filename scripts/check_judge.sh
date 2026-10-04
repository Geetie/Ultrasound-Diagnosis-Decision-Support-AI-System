#!/usr/bin/env bash
# 8006 判官：核实约定形态并给出恢复方案
# skill 约定：8006 = 判官 llama_cpp_server.py + Qwen3-8B-Q4_K_M.gguf（评测用 /no_think）
# 现状：我之前把它改成了 vllm api_server + Qwen3-8B（偏离约定，且占 19.5GB）
set -u
UAI="$HOME/uai"
say() { echo "[$(date +%H:%M:%S)] $*"; }

say "=== 判官启动脚本是否存在 ==="
for f in "$UAI/src/scripts/server_start_judge.sh" "$UAI/server_start_judge.sh" \
         "$UAI/src/scripts/_deploy_judge.sh"; do
  [ -f "$f" ] && say "  FOUND $f"
done
ls "$UAI/src/scripts/" | grep -i judge | sed 's/^/  /'

say "=== 判官脚本里的模型与端口 ==="
for f in "$UAI/src/scripts/server_start_judge.sh" "$UAI/server_start_judge.sh"; do
  if [ -f "$f" ]; then
    say "  --- $f ---"
    grep -nE "llama_cpp|gguf|model|port|8006|n_ctx" "$f" | head -12 | sed 's/^/    /'
  fi
done

say "=== 8006 当前占用 ==="
ss -ltnp 2>/dev/null | grep ":8006 " | sed 's/^/  /'
nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader | sed 's/^/  /'
say "  显存: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"

say "=== 主服务能否用新 8002（7B）跑通一次诊断 ==="
TOK=$(~/uai/venv/bin/python /tmp/_tok.py 2>/dev/null | tr -d '\r\n')
if [ -n "$TOK" ]; then
  say "  token ok"
else
  say "  LOGIN_FAIL"
fi
