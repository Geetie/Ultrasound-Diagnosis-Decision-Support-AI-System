#!/usr/bin/env bash
# 真机启动 8003 ultrasound-clip 图像嵌入服务（幂等：先杀旧进程）
#
# 用法：
#   bash ~/server_start_ultrasound_clip.sh [model_dir]
#   model_dir 默认 ~/uai/models/clip-vit-large-patch14；
#   官方权重就位后传 ~/uai/models/ultrasound-clip-official
set -u
UAI="$HOME/uai"
MODEL_DIR="${1:-$UAI/models/clip-vit-large-patch14}"
PORT=8003
LOG="$UAI/logs/ultrasound_clip.log"

export CUDA_VISIBLE_DEVICES=0   # fish 配置里的 CUDA_VISIBLE_DEVICES=2 是错的，见 skill §8

if [ ! -d "$MODEL_DIR" ]; then
  echo "ERROR: 模型目录不存在：$MODEL_DIR"
  exit 1
fi

# 幂等：杀掉旧的 8003 进程
pkill -f "start_embedding_services.py --service ultrasound-clip" 2>/dev/null && sleep 2

cd "$UAI/src"
nohup "$UAI/venv/bin/python" scripts/start_embedding_services.py \
  --service ultrasound-clip \
  --model-dir "$MODEL_DIR" \
  --port $PORT \
  --device cuda \
  --dtype float16 \
  --max-length 77 \
  > "$LOG" 2>&1 &

echo "已启动 pid=$!，日志：$LOG"
for i in $(seq 1 60); do
  sleep 2
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" 2>/dev/null)
  if [ "$code" = "200" ]; then
    echo "health=200（$((i * 2))s 就绪）"
    curl -s "http://127.0.0.1:$PORT/v1/model/info"
    echo
    exit 0
  fi
done
echo "WARN: 120s 内 health 未就绪，查看日志：tail -40 $LOG"
exit 1
