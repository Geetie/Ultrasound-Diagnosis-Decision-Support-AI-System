#!/usr/bin/env bash
# 仅执行 `alembic upgrade head`（不动现有 schema，与 server_migrate_from_scratch.sh 不同）
set -u
UAI="$HOME/uai"
cd "$UAI/src" || exit 1
set -a
# shellcheck disable=SC1091
. "$UAI/.env"
set +a

echo "=== 迁移前版本 ==="
"$UAI/venv/bin/python" -m alembic current 2>&1 | tail -2

echo
echo "=== upgrade head ==="
"$UAI/venv/bin/python" -m alembic upgrade head 2>&1 | tail -12

echo
echo "=== 迁移后版本 ==="
"$UAI/venv/bin/python" -m alembic current 2>&1 | tail -2

echo
echo "=== consent_records 列（应含 updated_at）==="
docker exec uai-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -q -c \
  "select string_agg(column_name, ',' order by ordinal_position) from information_schema.columns where table_name='consent_records'"
echo "=== done ==="
