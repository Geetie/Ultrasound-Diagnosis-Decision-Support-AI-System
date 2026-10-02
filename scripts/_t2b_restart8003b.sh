#!/bin/bash
set -u
UAI="$HOME/uai"
SRC="$UAI/src"

for pid in $(ss -ltnp | grep ':8003' | grep -oP 'pid=\K[0-9]+' | sort -u); do
  kill -9 "$pid" 2>/dev/null && echo "killed $pid"
done
pkill -9 -f 'ultrasound-clip' 2>/dev/null
sleep 4

cd "$SRC"
export PYTHONPATH="$SRC"
export CUDA_VISIBLE_DEVICES=0
nohup "$UAI/venv/bin/python" -u scripts/start_embedding_services.py \
  --service ultrasound-clip --clip-format open_clip --max-length 256 \
  --model-dir "$UAI/models/biomedclip-base-patch16-224" \
  --port 8003 --device cuda \
  > "$UAI/logs/clip_service.log" 2>&1 &
for i in $(seq 1 40); do
  curl -s --max-time 3 http://127.0.0.1:8003/health | grep -q status && break
  sleep 4
done
curl -s --max-time 3 http://127.0.0.1:8003/health | head -c 150; echo ""
echo "=== 用真实 CACTUS 测试图验证 /v1/analyze_view ==="
IMG=$(find "/mnt/ee16/home/student03/uai/data/cactus/Cactus Dataset/Images Dataset/A4C" -name '*.jpg' | head -1)
curl -s --max-time 60 -X POST http://127.0.0.1:8003/v1/analyze_view -F "images=@$IMG" | head -c 500; echo ""
IMG2=$(find "/mnt/ee16/home/student03/uai/data/cactus/Cactus Dataset/Images Dataset/Random" -name '*.jpg' | head -1)
curl -s --max-time 60 -X POST http://127.0.0.1:8003/v1/analyze_view -F "images=@$IMG2" | head -c 500; echo ""
