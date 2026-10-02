#!/bin/bash
# 切换生产 KB → v1.3.0 并重启主服务
set -u
UAI="$HOME/uai"
cd "$UAI"
grep -n 'RAG_KB_VERSION' "$UAI/.env"
sed -i 's/^RAG_KB_VERSION=.*/RAG_KB_VERSION=kb_v1.3.0/' "$UAI/.env"
grep -n 'RAG_KB_VERSION' "$UAI/.env"

# 重启主服务（18000）
PID=$(ss -ltnp | grep ':18000' | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "$PID" ] && { kill $PID; sleep 4; kill -9 $PID 2>/dev/null; }
cd "$UAI/src"
set -a; source "$UAI/.env"; set +a
nohup "$UAI/venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 \
  > "$UAI/logs/api.log" 2>&1 &
sleep 8
for i in 1 2 3 4 5 6; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:18000/api/v1/health")
  echo "health: $code"
  [ "$code" = "200" ] && break
  sleep 5
done
