#!/usr/bin/env bash
set -u
echo "=== ports"
ss -ltn | grep -E ":18000|:8002|:8006|:8003|:8004" | awk "{print \$4}"
echo "=== health"
curl -s --max-time 5 http://127.0.0.1:18000/api/v1/health | head -c 60; echo
echo "=== GPU"
nvidia-smi --query-gpu=memory.used --format=csv,noheader
echo "=== tmp scripts"
ls /tmp/ | grep -cE "^_[a-z0-9]+\.(py|sh)" || echo 0
echo "=== disk free"
df -h /mnt/ee16/home/student03 | tail -1 | awk "{print \$4}"
