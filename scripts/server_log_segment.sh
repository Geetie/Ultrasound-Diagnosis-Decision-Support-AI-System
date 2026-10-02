#!/usr/bin/env bash
# 只看"最近一次 RESTART 之后"的启动段：错误计数 + 关键初始化行 + 健康探测
set -u
LOG="$HOME/uai/logs/api.log"
CLEAN="$HOME/uai/logs/api.clean.log"

tr -d '\000' < "$LOG" > "$CLEAN"
start=$(grep -n '===== RESTART' "$CLEAN" | tail -1 | cut -d: -f1)
if [ -z "${start:-}" ]; then
  start=1
fi
sed -n "${start},\$p" "$CLEAN" > /tmp/seg.log

echo "== 新段行数: $(wc -l < /tmp/seg.log) =="
echo "== error/critical/warning 事件计数 =="
grep -oE '\[(error|critical|warning) +\] +[a-z_]+' /tmp/seg.log | sort | uniq -c | sort -rn

echo "== 关键初始化行 =="
grep -aE 'startup complete|rag_service|qdrant|pgvector|desens|worker_pool_started|metrics server|Victoria|placeholder_specialists|soft_hsm' /tmp/seg.log | tail -30

echo "== 健康探测 =="
curl -s -w '\nhttp=%{http_code}\n' --max-time 5 http://127.0.0.1:18000/api/v1/health | head -c 700
echo
echo "== 404 是否再被黑洞吞掉（错误处理器回归）=="
curl -s -o /tmp/e404.json -w 'http=%{http_code}\n' --max-time 5 http://127.0.0.1:18000/api/v1/definitely-not-exist
head -c 300 /tmp/e404.json
echo
echo "=== seg done ==="
