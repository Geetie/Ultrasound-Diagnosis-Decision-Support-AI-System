#!/usr/bin/env bash
# 真机启动 8003 图像嵌入服务（幂等：先杀旧进程）
#
# 用法：
#   bash ~/server_start_ultrasound_clip.sh [model_dir]
#
# 模型目录自动识别格式（按目录内是否有 open_clip_config.json）：
#   ~/uai/models/biomedclip-base-patch16-224   BiomedCLIP（open_clip 格式，512d，
#                                              MIT 许可；现行底座，I2T@10=23.6%）
#   ~/uai/models/clip-vit-large-patch14        通用 CLIP 基座（HF 格式，768d，9.6%）
#   ~/uai/models/ultrasound-clip-official      官方 Ultrasound-CLIP（OneDrive 权重
#                                              被墙无法自动化下载，手动放好后可传此目录）
set -u
UAI="$HOME/uai"
MODEL_DIR="${1:-$UAI/models/biomedclip-base-patch16-224}"
PORT=8003
LOG="$UAI/logs/ultrasound_clip.log"

export CUDA_VISIBLE_DEVICES=0   # fish 配置里的 CUDA_VISIBLE_DEVICES=2 是错的，见 skill §8
export HF_ENDPOINT=https://hf-mirror.com   # open_clip 的 tokenizer（BiomedBERT）从 HF 拉
export HF_HUB_DISABLE_XET=1

if [ ! -d "$MODEL_DIR" ]; then
  echo "ERROR: 模型目录不存在：$MODEL_DIR"
  exit 1
fi

# 格式识别：open_clip 格式（含 open_clip_config.json）→ --clip-format open_clip
FORMAT_ARGS=()
if [ -f "$MODEL_DIR/open_clip_config.json" ]; then
  FORMAT_ARGS=(--clip-format open_clip --max-length 256)
else
  FORMAT_ARGS=(--max-length 77)
fi

# 幂等：杀掉旧的 8003 进程
pkill -f "start_embedding_services.py --service ultrasound-clip" 2>/dev/null && sleep 2

cd "$UAI/src"
nohup "$UAI/venv/bin/python" -u scripts/start_embedding_services.py \
  --service ultrasound-clip \
  "${FORMAT_ARGS[@]}" \
  --model-dir "$MODEL_DIR" \
  --port $PORT \
  --device cuda \
  > "$LOG" 2>&1 &

echo "已启动 pid=$!（model_dir=$MODEL_DIR，${FORMAT_ARGS[*]}），日志：$LOG"
for i in $(seq 1 90); do
  sleep 2
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" 2>/dev/null)
  if [ "$code" = "200" ]; then
    echo "health=200（$((i * 2))s 就绪）"
    curl -s "http://127.0.0.1:$PORT/v1/model/info"
    echo
    exit 0
  fi
done
echo "WARN: 180s 内 health 未就绪，查看日志：tail -40 $LOG"
exit 1
