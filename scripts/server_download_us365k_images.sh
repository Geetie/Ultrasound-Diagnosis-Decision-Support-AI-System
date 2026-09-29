#!/usr/bin/env bash
# 经 hf-mirror 下载 US-365K 数据集到 ~/uai/data/us365k（可续传、幂等）
#
# 仓库结构（2026-09-29 实测 hf-mirror API）：
#   images.zip     15.35 GB（36.4 万张图，zip 打包）
#   full_data.json 318 MB（含 9 任务结构化标注，官方训练管线用）
#   train.jsonl     44 MB / valid.jsonl 15 MB / test.jsonl 14 MB（image/caption 对）
#
# 坑（沿用 server_download_models.sh 的实测结论）：
# - 必须 HF_ENDPOINT=https://hf-mirror.com（huggingface.co 不通）
# - 必须 HF_HUB_DISABLE_XET=1（cas-server.xethub.hf.co 无镜像，401 中断）
# - 下载分块续传，中断后重跑同一命令接着下
#
# 用法：nohup bash server_download_us365k_images.sh > ~/uai/logs/download_us365k.log 2>&1 &
set -u
UAI="$HOME/uai"
DATA="$UAI/data/us365k"
LOG_DIR="$UAI/logs"
mkdir -p "$DATA" "$LOG_DIR"

export HF_ENDPOINT=https://hf-mirror.com
export HF_HOME="$UAI/models/hf"
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
log "dl=$DL  HF_ENDPOINT=$HF_ENDPOINT  目标=$DATA"

log "==> US-365K 元数据（jsonl + full_data.json，约 390MB）"
"$DL" download JJY-0823/US-365K --repo-type dataset \
  full_data.json \
  train.jsonl \
  valid.jsonl \
  test.jsonl \
  --local-dir "$DATA" || log "WARN: 元数据下载失败（继续）"

log "==> US-365K images.zip（15.35GB，分块续传）"
"$DL" download JJY-0823/US-365K --repo-type dataset \
  images.zip \
  --local-dir "$DATA" || log "WARN: images.zip 下载失败（继续）"

ZIP="$DATA/images.zip"
if [ -f "$ZIP" ]; then
  log "==> images.zip 实际大小：$(du -h "$ZIP" | cut -f1)（应为 ~15GB）"
  if [ ! -d "$DATA/images" ]; then
    log "==> 解压 images.zip（约 36.4 万张）"
    unzip -q -o "$ZIP" -d "$DATA/images" && log "==> 解压完成" \
      || log "WARN: 解压失败（磁盘/包完整性待查）"
  else
    log "==> images/ 已存在，跳过解压"
  fi
  log "==> 图像文件数：$(find "$DATA/images" -type f | wc -l)"
else
  log "WARN: images.zip 不存在，下载未完成"
fi

log "==> 占用情况"
du -sh "$DATA"/* 2>/dev/null || true
log "==> done"
