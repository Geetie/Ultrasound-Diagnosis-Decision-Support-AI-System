#!/usr/bin/env bash
set -u
NEW="2026_10_02-02_00_19-model_local-dir:-mnt-ee16-home-student03-uai-models-biomedclip-base-patch16-224-lr_1e-05-b_256-j_12-p_amp_bf16"
sed -i "s|2026_10_02-01_32_00-model_local-dir:-mnt-ee16-home-student03-uai-models-biomedclip-base-patch16-224-lr_1e-05-b_32-j_8-p_amp_bf16|$NEW|" /tmp/_t2a_us365k_eval.py
grep -c "02_00_19" /tmp/_t2a_us365k_eval.py
bash -c "export CUDA_VISIBLE_DEVICES=0; ~/uai/venv/bin/python /tmp/_t2a_us365k_eval.py" 2>&1 | grep -vE "compact|info |UserWarning|ys.append" | tail -3
