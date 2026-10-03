#!/usr/bin/env bash
echo "=== api.log 尾部 ==="
tail -30 ~/uai/logs/api.log
echo "=== 监听 ==="
ss -ltn 2>/dev/null | grep 18000 || echo "18000 NOT LISTENING"
echo "=== 进程 ==="
ps aux | grep "[u]vicorn app.main" | head -3
