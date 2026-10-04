#!/usr/bin/env bash
# 核实 8002 / 8006 的真实形态与 skill 约定是否一致
# 依据 SKILL remote-gpu-server-devtest：
#   8002 应为**官方** vllm.entrypoints.openai.api_server + Qwen2.5-7B-Instruct
#   8006 应为**判官** llama_cpp_server.py + Qwen3-8B GGUF Q4_K_M
set -u
say() { echo "[$(date +%H:%M:%S)] $*"; }

say "=== 8002 进程形态 ==="
ss -ltnp 2>/dev/null | grep ":8002 " | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1 | while read p; do
  echo "  pid=$p"
  echo "  cmd=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | cut -c1-150)"
done
pgrep -af "vllm.entrypoints.openai.api_server" | cut -c1-110 | sed 's/^/  /'
pgrep -af "vllm_server.py" | cut -c1-110 | sed 's/^/  /'
pgrep -af "llama_cpp_server" | cut -c1-110 | sed 's/^/  /'

say "=== 8002 /v1/models（官方 server 才有这个端点）==="
curl -s --max-time 15 http://127.0.0.1:8002/v1/models 2>&1 | head -c 300; echo ""

say "=== 8002 chat 实测 ==="
curl -s --max-time 60 -X POST http://127.0.0.1:8002/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen2.5-7B-Instruct","messages":[{"role":"user","content":"说OK"}],"max_tokens":8}' \
  2>&1 | head -c 260; echo ""

say "=== 8006 进程形态（判官应为 llama_cpp_server）==="
ss -ltnp 2>/dev/null | grep ":8006 " | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1 | while read p; do
  echo "  pid=$p"
  echo "  cmd=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | cut -c1-150)"
done

say "=== GPU 显存 ==="
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader | sed 's/^/  /'

say "=== 判官模型目录（Qwen3-8B GGUF？）==="
ls -d ~/uai/models/*Qwen3* ~/uai/models/*gguf* 2>/dev/null | head -4
find ~/uai/models -maxdepth 2 -name "*.gguf" 2>/dev/null | head -3

say "=== /tmp 探针累积（skill 要求全量清点）==="
ls /tmp/_*.py /tmp/_*.sh 2>/dev/null | wc -l
