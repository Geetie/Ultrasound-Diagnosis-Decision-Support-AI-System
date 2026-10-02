#!/bin/bash
# Task 6: restart main API (18000) after sync, per reference.md R1
UAI="$HOME/uai"
PID=$(ss -ltnp | grep ':18000' | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "$PID" ] && { kill $PID; sleep 4; kill -9 $PID 2>/dev/null; }
cd "$UAI/src" || exit 1
set -a; source "$UAI/.env"; set +a
nohup "$UAI/venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 > "$UAI/logs/api.log" 2>&1 &
echo "new pid: $!"
sleep 10
ps -o lstart= -p $! 2>/dev/null
curl -s http://127.0.0.1:18000/api/v1/health | head -c 300
echo
