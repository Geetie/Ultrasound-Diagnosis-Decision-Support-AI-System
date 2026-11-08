#!/bin/bash
set -u
cd ~/uai/src
export CUDA_VISIBLE_DEVICES=0
export PYTHONPATH=$HOME/uai/src
~/uai/venv/bin/python -u scripts/audit/p103_failure_modes.py \
  --parts t2b,tn5000,aul,tn3k --p101-report reports/p101_imbalance_aug_arms.json \
  --report reports/p103_failure_modes.json 2>&1 | grep -v "FutureWarning\|import pynvml" | tail -20
