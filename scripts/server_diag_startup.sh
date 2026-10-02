#!/usr/bin/env bash
# 定位主服务启动失败原因。日志含 NUL，必须先 tr -d 否则 grep 判成二进制。
set -uo pipefail
U=/mnt/ee16/home/student03/uai

echo "=== 候选日志 ==="
ls -la "$U"/logs/*.log 2>/dev/null | head -20
ls -la "$U"/*.log 2>/dev/null | head -20

echo
echo "=== 末尾 50 行（已去 NUL） ==="
for f in "$U/logs/api.log" "$U/api.log" "$U/logs/api.clean.log"; do
  [ -f "$f" ] || continue
  echo "--- $f ---"
  tr -d '\000' < "$f" | tail -50
  break
done

echo
echo "=== 异常关键字 ==="
for f in "$U/logs/api.log" "$U/api.log" "$U/logs/api.clean.log"; do
  [ -f "$f" ] || continue
  echo "--- $f ---"
  tr -d '\000' < "$f" | grep -nE "Traceback|Error|error|Exception|ImportError|AttributeError|ValidationError|CRITICAL" | tail -25
  break
done
