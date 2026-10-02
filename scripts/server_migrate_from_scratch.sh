#!/usr/bin/env bash
# 空库从零跑迁移链：drop schema → alembic upgrade head → 逐项校验
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
cd "$SRC" || exit 1
set -a
# shellcheck disable=SC1091
. "$UAI/.env"
set +a
export CUDA_VISIBLE_DEVICES=0

PSQL="docker exec uai-postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -tA"

echo "--- 0) 迁移前版本 ---"
$PSQL -c "select version_num from alembic_version" 2>&1 | tail -2

echo
echo "--- 1) 重建 schema（空库）---"
$PSQL -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;" 2>&1 | tail -3

echo
echo "--- 2) alembic upgrade head ---"
"$UAI/venv/bin/python" -m alembic upgrade head 2>&1 | tail -30

echo
echo "--- 3) 迁移后版本（应为 0012）---"
$PSQL -c "select version_num from alembic_version"

echo
echo "--- 4) 表清单 ---"
$PSQL -c "select tablename from pg_tables where schemaname='public' order by 1"
echo "表数（含 alembic_version）= $($PSQL -c "select count(*) from pg_tables where schemaname='public'")"

echo
echo "--- 5) 关键表的列 ---"
$PSQL -c "select table_name || ' -> ' || string_agg(column_name, ',' order by ordinal_position) from information_schema.columns where table_schema='public' and table_name in ('sessions','audit_logs','consent_records','diagnosis_results') group by table_name order by table_name"

echo
echo "--- 6) findings 类型（应 jsonb）---"
$PSQL -c "select data_type from information_schema.columns where table_name='diagnosis_results' and column_name='findings'"

echo
echo "--- 7) 审计不可变规则（应 4 条）---"
$PSQL -c "select rulename from pg_rules where schemaname='public' and rulename like 'audit%' order by 1"

echo
echo "--- 8) 向量扩展 + hnsw 索引 ---"
$PSQL -c "select extname from pg_extension where extname='vector'"
$PSQL -c "select indexname from pg_indexes where schemaname='public' and indexname like '%hnsw%' order by 1"

echo
echo "--- 9) 再跑一次 upgrade（幂等性：不应报错）---"
"$UAI/venv/bin/python" -m alembic upgrade head 2>&1 | tail -5
echo "exit=$?"
echo "=== migrate done ==="
