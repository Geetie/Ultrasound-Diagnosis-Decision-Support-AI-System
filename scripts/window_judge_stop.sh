#!/usr/bin/env bash
# judge stop (finetune window start)
set -u
OLD=$(ss -ltnp 2>/dev/null | grep ':8006' | grep -oP 'pid=\K[0-9]+' | head -1)
if [ -n "$OLD" ]; then
    kill "$OLD"; sleep 3; kill -9 "$OLD" 2>/dev/null
fi
pkill -9 -f EngineCore 2>/dev/null
sleep 2
if ss -ltnp | grep -q ':8006'; then
    echo "judge-STILL-UP"
else
    echo "judge-stopped"
fi
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader