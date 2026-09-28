#!/usr/bin/env bash
# 连通性探针外壳：容器侧 redis-cli + 应用 venv 侧探针
set -u
UAI="$HOME/uai"
set -a
# shellcheck disable=SC1091
. "$UAI/.env"
set +a

echo "--- docker ps ---"
docker ps --format '{{.Names}} | {{.Status}} | {{.Ports}}'

echo
echo "--- redis-cli（容器内，不回显口令）---"
docker exec uai-redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning PING
docker exec uai-redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning CONFIG GET maxclients
docker exec uai-redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning CONFIG GET timeout
docker exec uai-redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning INFO clients | tr -d '\r' | head -6

echo
echo "--- pg 容器 ---"
docker exec uai-postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"

echo
echo "--- 应用 venv 侧探针（含 CUDA_VISIBLE_DEVICES=0）---"
cd "$UAI/src" || exit 1
env CUDA_VISIBLE_DEVICES=0 "$UAI/venv/bin/python" "$HOME/server_probe_conn.py" 2>&1

echo
echo "=== probe done ==="
