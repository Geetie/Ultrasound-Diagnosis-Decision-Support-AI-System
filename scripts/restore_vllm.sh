#!/usr/bin/env bash
# 拉起被压测停掉的两个 vLLM：8002 主生成（0.46）→ 8006 判官（0.40）
# 注意：~/uai/src/scripts/*.sh 历史上是 CRLF（bash 报 do\r 语法错），这里用 sed 去 CR 后执行
set -u
UAI="$HOME/uai"

sed -i 's/\r$//' "$UAI/src/scripts/window_8002_restart.sh" "$UAI/src/scripts/window_judge_start.sh" "$UAI/src/scripts/window_judge_stop.sh"
bash -n "$UAI/src/scripts/window_8002_restart.sh" && echo "8002-script-syntax-OK"
bash -n "$UAI/src/scripts/window_judge_start.sh" && echo "judge-script-syntax-OK"

echo "=== 8002 主生成（Qwen2.5-7B-Instruct, 0.46）==="
bash "$UAI/src/scripts/window_8002_restart.sh" 2>&1 | tail -2

echo "=== 8006 判官（Qwen3-8B, 0.40）==="
bash "$UAI/src/scripts/window_judge_start.sh" 2>&1 | tail -2

echo "=== 六端点复验 ==="
for p in 18000 8002 8003 8006 8004 9001; do
  c=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:${p}/health" 2>/dev/null)
  [ "$p" = "18000" ] && c=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:${p}/api/v1/health" 2>/dev/null)
  echo "PORT_${p}=${c}"
done
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
