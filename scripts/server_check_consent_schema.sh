#!/usr/bin/env bash
# 核对 consent_records 的真实列（app 的模型/迁移 vs SG 的独立 Base 声明是否打架）
set -u
UAI="$HOME/uai"
set -a
# shellcheck disable=SC1091
. "$UAI/.env"
set +a

echo "=== consent_records 实际列 ==="
docker exec uai-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -q -c \
  "select column_name || ' ' || data_type from information_schema.columns where table_name='consent_records' order by ordinal_position"

echo
echo "=== consent_records 行数 ==="
docker exec uai-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -q -c \
  "select count(*) || ' 行' from consent_records"

echo
echo "=== SG 日志里的同意查询错误 ==="
tr -d '\000' < "$UAI/logs/sg.log" > "$UAI/logs/sg.clean.log" 2>/dev/null || true
grep -aiE 'consent|column|does not exist' "$UAI/logs/sg.clean.log" | tail -8 | cut -c1-240
echo "=== done ==="
