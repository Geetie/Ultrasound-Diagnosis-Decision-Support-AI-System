#!/usr/bin/env bash
# 打通版：2000 样本小规模，全参数体系（tensorboard/val/resume/save-most-recent）
set -u
UAI="$HOME/uai"
pkill -f open_clip_train 2>/dev/null
sleep 2
cd "$UAI/src" || exit 1
set -a
source "$UAI/.env"
set +a
export CUDA_VISIBLE_DEVICES=0
head -2001 "$UAI/data/us365k/openclip_train_30k.csv" > "$UAI/data/us365k/openclip_smoke.csv"
nohup "$UAI/venv/bin/python" -m open_clip_train.main \
  --train-data "$UAI/data/us365k/openclip_smoke.csv" \
  --val-data "$UAI/data/us365k/openclip_valid_2k.csv" \
  --csv-img-key filepath --csv-caption-key caption \
  --model "local-dir:$UAI/models/biomedclip-base-patch16-224" \
  --lock-text --lock-text-freeze-layer-norm \
  --warmup 10 --lr 1e-5 --wd 0.05 --grad-clip-norm 1.0 \
  --batch-size 128 --workers 8 --epochs 1 --precision amp_bf16 \
  --val-frequency 1 --report-to tensorboard --log-every-n-steps 5 \
  --save-frequency 1 --save-most-recent \
  --logs "$UAI/models/t2a_us365k_ft" \
  > "$UAI/logs/t2a_smoke.log" 2>&1 &
echo "smoke-started-pid $!"
