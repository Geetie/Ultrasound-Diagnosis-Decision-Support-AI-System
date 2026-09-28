#!/usr/bin/env bash
# 核对患者条件是否落到库里、是否被转发给 SG、SG 是否仍在报"缺条件数据"
set -u
UAI="$HOME/uai"
set -a
# shellcheck disable=SC1091
. "$UAI/.env"
set +a

echo "=== 最近 3 条请求的 patient_context ==="
docker exec uai-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -q -c \
  "select request_id || ' | ' || coalesce(patient_context::text, 'NULL') || ' | ' || coalesce(status,'?') from diagnosis_results order by id desc limit 3"

echo
echo "=== 应用侧：是否走到转发点 ==="
tr -d '\000' < "$UAI/logs/api.log" > "$UAI/logs/api.clean.log"
start=$(grep -an '===== RESTART' "$UAI/logs/api.clean.log" | tail -1 | cut -d: -f1)
sed -n "${start:-1},\$p" "$UAI/logs/api.clean.log" > "$UAI/logs/api.segment.log"
grep -aE 'patient_conditions_forwarded|safety_gateway_check_failed|pipeline_safety_check_failed' \
  "$UAI/logs/api.segment.log" | tail -5 | cut -c1-220
echo "(以上为空则说明转发日志未出现)"

echo
echo "=== SG 侧：禁忌判定看到的条件 ==="
tr -d '\000' < "$UAI/logs/sg.log" > "$UAI/logs/sg.clean.log"
grep -aE 'contraindication_no_condition_data|contraindication_detected|sg_safety_check_entered' \
  "$UAI/logs/sg.clean.log" | tail -6 | cut -c1-220
echo "=== done ==="
