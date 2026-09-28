#!/usr/bin/env bash
# 打印"最近一次 RESTART 之后"的段内关键错误明细（带 error= 字段），只截断不加工
set -u
UAI="$HOME/uai"
CLEAN="$UAI/logs/api.clean.log"
SEG="$UAI/logs/api.segment.log"

tr -d '\000' < "$UAI/logs/api.log" > "$CLEAN"
start=$(grep -an '===== RESTART' "$CLEAN" | tail -1 | cut -d: -f1)
sed -n "${start:-1},\$p" "$CLEAN" > "$SEG"
echo "--- 段行数: $(wc -l < "$SEG") ---"

echo
echo "=== 错误事件明细 ==="
grep -aE 'unhandled_exception|dense_search_failed|safety_gateway_check_failed|pipeline_consent_status_unavailable|self_rag_retries_exhausted|vector|Vector' "$SEG" \
  | cut -c1-300 | head -16

echo
echo "=== 其余 error/critical（前 8）==="
grep -aE '\[(error|critical) +\]' "$SEG" | cut -c1-240 | head -8
echo "=== done ==="
