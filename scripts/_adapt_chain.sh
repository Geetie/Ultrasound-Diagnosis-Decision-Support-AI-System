#!/usr/bin/env bash
# 自动链：等 LFS 下载完成 → 域适应微调 → 出报告
set -u
UAI="$HOME/uai"
while true; do
  small=$(find /tmp/tn3k_probe/trainval-image -size -200c 2>/dev/null | wc -l)
  if [ "$small" -eq 0 ]; then break; fi
  sleep 60
done
echo "LFS-DOWNLOAD-DONE $(date)"
cp -r /tmp/tn3k_probe "$UAI/data/tn3k_dataset"
cd "$UAI/src" || exit 1
set -a
source "$UAI/.env"
set +a
export CUDA_VISIBLE_DEVICES=0
export PYTHONPATH="$UAI/src"
"$UAI/venv/bin/python" scripts/t2a_domain_adapt.py 2>&1 | tail -8
echo "ADAPT-DONE $(date)"
