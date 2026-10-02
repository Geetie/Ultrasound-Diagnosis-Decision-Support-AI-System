#!/usr/bin/env bash
# 下载图像嵌入腿（8003）用的 CLIP 权重到 ~/uai/models
#
# 权重策略（2026-09-29 定）：
# 1) 官方 Ultrasound-CLIP（CVPR 2026, ZJUDataIntelligence）预训练权重在 **OneDrive**，
#    且已迁移到 SharePoint（migratedtospo=true）——API 自动化下载不可行，
#    需浏览器手动下载后 scp 到 ~/uai/models/ultrasound-clip-official/。
#    链接：https://1drv.ms/f/c/48250be4328ce7ab/IgCnIA0--KugSoW0japEO8HaAW6koYf9YVNnB9vQiqeq_O8?e=c7xe4J
#    ⚠️ 该仓库无 license 声明，注册商用前需向作者确认（研发/评测期可用）。
# 2) 本脚本自动下载**基座** openai/clip-vit-large-patch14（ViT-L/14，图像投影 768 维，
#    与 settings.embedding_dim=768 一致）作为 8003 的先行车轮 + I2T@10 基线；
#    官方权重就位后经 --model-dir 切换对比，不达标再按降级路径评估 BiomedCLIP。
#
# 坑（同 server_download_models.sh）：HF_ENDPOINT 镜像 + HF_HUB_DISABLE_XET=1。
set -u
UAI="$HOME/uai"
MODELS="$UAI/models"
mkdir -p "$MODELS"

export HF_ENDPOINT=https://hf-mirror.com
export HF_HOME="$MODELS/hf"
export HF_HUB_DISABLE_TELEMETRY=1
export HF_HUB_ENABLE_HF_TRANSFER=0
export HF_HUB_DISABLE_XET=1

if [ -x "$UAI/venv/bin/hf" ]; then
  DL="$UAI/venv/bin/hf"
elif [ -x "$UAI/venv/bin/huggingface-cli" ]; then
  DL="$UAI/venv/bin/huggingface-cli"
else
  echo "ERROR: 找不到 hf / huggingface-cli（应在 $UAI/venv/bin）"
  exit 1
fi

log() { echo "[$(date '+%F %T')] $*"; }

log "==> openai/clip-vit-large-patch14（约 1.7GB，基座 768d）"
"$DL" download openai/clip-vit-large-patch14 \
  --local-dir "$MODELS/clip-vit-large-patch14" \
  --exclude "*.flax*" \
  --exclude "*.msgpack*" \
  --exclude "*.h5*" \
  --exclude "tf_model*" \
  || log "WARN: CLIP 基座下载失败（继续）"

log "==> 占用情况"
du -sh "$MODELS/clip-vit-large-patch14" 2>/dev/null || true
log "==> done"
