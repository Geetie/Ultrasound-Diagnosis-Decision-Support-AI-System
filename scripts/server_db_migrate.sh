#!/usr/bin/env bash
# 切换 Postgres 为带 pgvector 的镜像 → 补全 .env URL → 执行 alembic 迁移。
set -u
ROOT=/mnt/ee16/home/student03/uai
SRC=$ROOT/src
PY=$ROOT/venv/bin/python
M=docker.1panel.live
ENVF=$ROOT/.env
COMPOSE=$ROOT/docker-compose.server.yml

echo "=== A. postgres 镜像换成带 pgvector 的版本 ==="
sed -i "s#${M}/library/postgres:16-alpine#${M}/pgvector/pgvector:pg16#" "$COMPOSE"
grep -n 'image:' "$COMPOSE"

echo
echo "=== B. 重建（PG16→PG16，数据卷保留）==="
docker compose --env-file "$ENVF" -f "$COMPOSE" up -d 2>&1 | tail -6
sleep 12
docker inspect --format '{{.Name}} {{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' uai-postgres

echo
echo "=== C. 验证 vector 扩展可安装 ==="
docker exec uai-postgres psql -U uai -d uai -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>&1 | tail -2
docker exec uai-postgres psql -U uai -d uai -tAc "SELECT extversion FROM pg_extension WHERE extname = 'vector';" 2>&1 | tail -2

echo
echo "=== D. 补全 .env 的连接串（口令仍不出文件）==="
if grep -q '^DATABASE_URL' "$ENVF"; then
  echo "已有 DATABASE_URL，跳过"
else
  set -a; . "$ENVF"; set +a
  {
    echo "DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:15432/${POSTGRES_DB}"
    echo "REDIS_URL=redis://:${REDIS_PASSWORD}@127.0.0.1:16379/0"
  } >> "$ENVF"
  echo "已追加 DATABASE_URL / REDIS_URL"
fi
echo "键：$(grep -o '^[A-Z_]*' "$ENVF" | tr '\n' ' ')"

echo
echo "=== E. 项目如何读配置（确认变量名与是否带前缀）==="
grep -rn 'database_url\|env_prefix\|model_config' "$SRC/app/config/settings.py" 2>/dev/null | head -10
ls -a "$SRC" | grep -i '^\.env' || echo "(无 .env.example)"

echo
echo "=== F. alembic 迁移 ==="
cd "$SRC" || exit 1
DB_URL=$(grep '^DATABASE_URL' "$ENVF" | cut -d= -f2-)
DATABASE_URL="$DB_URL" "$PY" -m alembic upgrade head > /tmp/alembic.log 2>&1
echo "rc=$?"
tail -14 /tmp/alembic.log

echo
echo "=== G. 表清单 ==="
docker exec uai-postgres psql -U uai -d uai -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>&1 | tail -1
docker exec uai-postgres psql -U uai -d uai -tAc "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename LIMIT 12;" 2>&1 | tail -12
echo "MIGRATE_DONE"
