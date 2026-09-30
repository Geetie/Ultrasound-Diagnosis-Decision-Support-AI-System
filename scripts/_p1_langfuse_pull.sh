#!/usr/bin/env bash
set -u
IMAGES=(
  "ghcr.io/langfuse/langfuse:3"
  "clickhouse/clickhouse-server:24.12"
  "minio/minio:latest"
  "postgres:17"
  "redis:7"
)
for img in "${IMAGES[@]}"; do
  echo "=== pull $img ==="
  docker pull "$img" > /tmp/pull_$(echo "$img" | tr '/:' '__').log 2>&1 &
done
wait
for img in "${IMAGES[@]}"; do
  docker image inspect "$img" > /dev/null 2>&1 && echo "OK $img" || echo "MISS $img"
done
