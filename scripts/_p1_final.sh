#!/usr/bin/env bash
set -u
cd ~/uai/langfuse
docker compose exec -T langfuse-web sh -c 'env | grep -E "CLICKHOUSE_MIGRATION_URL" || echo missing-in-container'
echo "=== compose file 里 ==="
grep -n 'MIGRATION' docker-compose.yml
