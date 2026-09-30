#!/usr/bin/env bash
set -u
echo "=== docker 状态 ==="
docker --version 2>/dev/null || echo no-docker
docker compose version 2>/dev/null || docker-compose --version 2>/dev/null || echo no-compose
echo "=== 内存/磁盘余量 ==="
free -g | head -2
df -h /mnt/ee16/home/student03 | tail -1
echo "=== 用户 docker 权限 ==="
docker ps 2>&1 | head -2
