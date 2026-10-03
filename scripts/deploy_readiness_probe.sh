#!/usr/bin/env bash
# 交付就绪度盘点（2026-10-04）：服务矩阵 + 关键依赖 + 阻塞项
set -u
cd ~/uai/src
set -a; source ~/uai/.env; set +a
export PYTHONPATH=~/uai/src
say() { echo "[$(date +%H:%M:%S)] $*"; }

say "=== A. 服务端口 ==="
for p in 18000 8002 8003 8006 9001; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 6 "http://127.0.0.1:$p/health" 2>/dev/null || echo 000)
  name=$(ss -ltnp 2>/dev/null | grep ":$p " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  printf "  %-6s http=%-4s pid=%s\n" "$p" "$code" "${name:-none}"
done

say "=== B. 数据/中间件依赖 ==="
for hp in "5432 postgresql" "6379 redis" "6333 qdrant" "9200 elasticsearch" "15432 pgvector-uai"; do
  set -- $hp
  if timeout 3 bash -c "echo > /dev/tcp/127.0.0.1/$1" 2>/dev/null; then
    echo "  $1($2) OPEN"
  else
    echo "  $1($2) CLOSED"
  fi
done

say "=== C. LLM 连通性（交付阻塞项）==="
echo "  -- 8002 (Qwen2.5-1.5B) --"
curl -s --max-time 20 -X POST http://127.0.0.1:8002/completions \
  -H "Content-Type: application/json" \
  -d '{"prompt":"say OK","max_tokens":8}' 2>&1 | head -c 200; echo ""
echo "  -- 8006 (Qwen3-8B, OpenAI 兼容) --"
curl -s --max-time 20 -X POST http://127.0.0.1:8006/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3-8B","messages":[{"role":"user","content":"say OK"}],"max_tokens":8}' 2>&1 | head -c 200; echo ""

say "=== D. 主服务依赖健康 ==="
curl -s --max-time 20 http://127.0.0.1:18000/api/v1/health 2>&1 | head -c 500; echo ""

say "=== E. 最近 LLM/推理失败（从主服务日志）==="
grep -iE "llm_unavailable|InferenceError|llm.*fail|openai.*error|Connection refused" \
  /tmp/uvicorn18000.log 2>/dev/null | tail -8 | cut -c1-190

say "=== F. 启动期降级告警 ==="
grep -iE "unhealthy|falling_back|degraded|_failed" /tmp/uvicorn18000.log 2>/dev/null \
  | head -10 | cut -c1-190
