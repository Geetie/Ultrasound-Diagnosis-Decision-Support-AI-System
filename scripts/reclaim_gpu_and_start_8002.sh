#!/usr/bin/env bash
# 清理 8002 残留引擎核心（释放显存）并重启 8002
set -u
say() { echo "[$(date +%H:%M:%S)] $*"; }

say "=== 1) 杀 8002 残留的 EngineCore（8006 的必须留）==="
# 判定：监听 8006 的 EngineCore 是 3928770（Qwen3-8B），
# 其余 EngineCore 都是残留（8002 的，启动失败后没释放）
KEEP=$(ss -ltnp 2>/dev/null | grep ":8006" | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1)
# 8006 的 api_server 父进程 pid
API_PID=$(ss -ltnp 2>/dev/null | grep ":8006" | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1)
say "  keep(8006 api_server)=$API_PID"
for p in $(pgrep -f "VLLM::EngineCore" 2>/dev/null; nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | while read x; do ps -p $x -o comm= 2>/dev/null | grep -q VLLM && echo $x; done); do
  if [ "$p" = "$API_PID" ]; then say "  skip $p (8006)"; continue; fi
  say "  kill $p"
  kill -9 "$p" 2>/dev/null || true
done
sleep 5
say "  显存: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"

say "=== 2) 重启 8002 ==="
bash ~/uai/src/scripts/server_start_vllm_engine.sh 2>&1 | tail -2
for i in $(seq 1 70); do
  sleep 3
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8002/health 2>/dev/null)
  if [ "$c" = "200" ]; then say "  8002 OK ($((i*3))s)"; break; fi
  if [ $((i % 10)) -eq 0 ]; then say "  ... waiting ($((i*3))s) mem=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"; fi
done

say "=== 3) 验两个端点 ==="
echo "  /v1/chat/completions:"
curl -s --max-time 60 -X POST http://127.0.0.1:8002/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"m","messages":[{"role":"user","content":"说OK"}],"max_tokens":10}' | head -c 260; echo ""
echo "  /completions:"
curl -s --max-time 60 -X POST http://127.0.0.1:8002/completions \
  -H "Content-Type: application/json" \
  -d '{"prompt":"say OK","max_tokens":8}' | head -c 140; echo ""
say "  显存: $(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
