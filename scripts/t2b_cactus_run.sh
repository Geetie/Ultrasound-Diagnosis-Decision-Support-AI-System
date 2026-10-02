#!/bin/bash
# T2b CACTUS 微调一键执行：停双 vLLM → 训练 → 串行恢复 → 健康核验
# 显存账：嵌入基线 6.8GB 常驻；训练需 ~20GB+，故双 vLLM（~39GB）必须暂停
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
PY="$UAI/venv/bin/python"

echo "=== 1. 停双 vLLM（训练完自动恢复）==="
pkill -9 -f 'VLLM::EngineCore' 2>/dev/null
pkill -9 -f 'vllm.entrypoints.openai.api_server' 2>/dev/null
sleep 3
nvidia-smi --query-gpu=memory.used --format=csv,noheader

echo "=== 2. 训练（3 epochs，bs 256，workers 8）==="
cd "$SRC"
export PYTHONPATH="$SRC"
CUDA_VISIBLE_DEVICES=0 "$PY" scripts/t2b_cactus_train.py \
  --data-dir "$UAI/data/t2b_cactus" \
  --model-dir "$UAI/models/biomedclip-base-patch16-224" \
  --output reports/t2b_cactus \
  --epochs 3 --batch-size 256 --num-workers 8 --lr 3e-6
TRAIN_RC=$?
echo "train rc=$TRAIN_RC"

echo "=== 3. 串行恢复双 vLLM ==="
nohup bash "$SRC/scripts/_deploy_dual_vllm.sh" > "$UAI/logs/dual_vllm_start.log" 2>&1 &
echo "恢复中（后台，日志 dual_vllm_start.log）"
exit $TRAIN_RC
