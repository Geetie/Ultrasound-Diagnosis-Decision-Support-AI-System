#!/usr/bin/env bash
# 下载真机评测用小型可商用数据集（liver: AUL，CC BY 4.0，约 70MB）到 ~/uai/data/aul
#
# AUL（Annotated Ultrasound Liver images）
#   Zenodo DOI 10.5281/zenodo.7272660（2026-09-29 核对：license=cc-by-4.0）
#   Benign.zip 16.9MB / Malignant.zip 46.9MB / Normal.zip 6.6MB
#   ⚠️ 许可原文另有要求：使用须联系作者、注明出处并引用（见台账 许可证存档.md）
set -u
UAI="$HOME/uai"
AUL="$UAI/data/aul"
mkdir -p "$AUL"

log() { echo "[$(date '+%F %T')] $*"; }

base="https://zenodo.org/records/7272660/files"
for f in Benign.zip Malignant.zip Normal.zip; do
  if [ -f "$AUL/$f" ]; then
    log "已有 $f，跳过"
    continue
  fi
  log "==> $f"
  curl -fL --retry 3 --max-time 600 -o "$AUL/$f" "$base/$f?download=1" \
    || log "WARN: $f 下载失败（继续）"
done

log "==> 解压"
for f in Benign.zip Malignant.zip Normal.zip; do
  [ -f "$AUL/$f" ] && unzip -q -o "$AUL/$f" -d "$AUL" && log "解压 $f 完成"
done

log "==> 图像文件数：$(find "$AUL" -type f \( -iname '*.jpg' -o -iname '*.png' -o -iname '*.jpeg' -o -iname '*.bmp' \) | wc -l)"
du -sh "$AUL"
log "==> done"
