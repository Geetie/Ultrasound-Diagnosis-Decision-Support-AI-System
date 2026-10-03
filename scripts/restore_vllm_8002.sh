#!/usr/bin/env bash
# 恢复 8002 vLLM 主引擎（用仓库内已 LF 规范化的脚本，避开 CRLF）
UAI="$HOME/uai"
LOG="$UAI/logs/vllm.log"
mkdir -p "$UAI/logs"
echo "=== 启动前 ==="
ss -ltn 2>/dev/null | grep -E ":(8002|8006)" || echo "8002/8006 均未监听"
bash "$UAI/src/scripts/server_start_vllm_engine.sh" 2>&1 | tail -3
echo "=== 等待就绪（最多 180s）==="
for i in $(seq 1 60); do
  sleep 3
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8002/health 2>/dev/null)
  if [ "$c" = "200" ]; then
    echo "VLLM_8002_OK after $((i*3))s"
    break
  fi
done
echo "=== 结果 ==="
for p in 8002 8003 8006 9001 18000; do
  printf "  %-6s %s\n" "$p" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:$p/health 2>/dev/null || echo down)"
done
