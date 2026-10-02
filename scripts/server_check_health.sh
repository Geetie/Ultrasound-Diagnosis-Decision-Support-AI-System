#!/usr/bin/env bash
# 汇总两侧健康（紧凑版）：调用 server_health_probe.py，再看最近相关日志
set -u
UAI="$HOME/uai"

"$UAI/venv/bin/python" "$HOME/server_health_probe.py"

echo
echo "=== 最近日志（SG 探针 / 融合探针 / RAG / AGT）==="
tr -d '\000' < "$UAI/logs/api.log" > "$UAI/logs/api.clean.log"
grep -aE 'safety_gateway_health|safety_gateway_consecutive|agents_health_check|placeholder_specialists|rag_health' \
  "$UAI/logs/api.clean.log" | tail -6
tr -d '\000' < "$UAI/logs/sg.log" > "$UAI/logs/sg.clean.log"
grep -aE 'fusion|threshold|stream_consume' "$UAI/logs/sg.clean.log" | tail -6
echo "=== check done ==="
