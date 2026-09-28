#!/usr/bin/env bash
# 查看最近一次诊断请求的落库状态 + 本次启动段的日志事件计数（只输出状态/事件名）
set -u
UAI="$HOME/uai"
set -a
# shellcheck disable=SC1091
. "$UAI/.env"
set +a

echo "=== 最近 3 条诊断请求（状态字段）==="
docker exec uai-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -q -c \
  "select request_id || ' | status=' || coalesce(status,'NULL') || ' | ai_failed_keep=' || coalesce((findings->>'ai_diagnosis_failed'),'-') || ' | review=' || coalesce((findings->>'requires_manual_review'),'-') from diagnosis_results order by id desc limit 3"

echo
echo "=== 本段事件计数（error/critical）==="
tr -d '\000' < "$UAI/logs/api.log" > "$UAI/logs/api.clean.log"
start=$(grep -an '===== RESTART' "$UAI/logs/api.clean.log" | tail -1 | cut -d: -f1)
sed -n "${start:-1},\$p" "$UAI/logs/api.clean.log" > "$UAI/logs/api.segment.log"
echo "段行数: $(wc -l < "$UAI/logs/api.segment.log")"
grep -aoE '\[(error|critical) +\] [a-z_]+' "$UAI/logs/api.segment.log" | sort | uniq -c | sort -rn | head -10
echo
echo "=== 本段事件计数（warning，前 10）==="
grep -aoE '\[warning +\] [a-z_]+' "$UAI/logs/api.segment.log" | sort | uniq -c | sort -rn | head -10
echo
echo "=== 本段管线相关事件（前 18）==="
grep -aoE '\[(info|warning|error) +\] [a-z_]+' "$UAI/logs/api.segment.log" \
  | grep -aE 'pipeline|worker|enqueue|dispatcher|inference|monitor|desens|safety_gateway' \
  | sort | uniq -c | sort -rn | head -18
