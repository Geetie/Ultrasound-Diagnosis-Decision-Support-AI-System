#!/usr/bin/env bash
# judge restart (finetune window end) - sequence from _deploy_judge.sh
set -u
UAI="$HOME/uai"
OLD=$(ss -ltnp 2>/dev/null | grep ':8006' | grep -oP 'pid=\K[0-9]+' | head -1)
if [ -n "$OLD" ]; then
    kill "$OLD"; sleep 3; kill -9 "$OLD" 2>/dev/null
fi
cd "$UAI/venv-vllm" 2>/dev/null || true
export CUDA_VISIBLE_DEVICES=0
export VLLM_USE_FLASHINFER_SAMPLER=0
PATH="$UAI/venv-vllm/bin:$PATH" nohup "$UAI/venv-vllm/bin/python" -m vllm.entrypoints.openai.api_server \
  --model "$UAI/models/Qwen3-8B" \
  --served-model-name Qwen3-8B \
  --port 8006 --max-model-len 4096 \
  --gpu-memory-utilization 0.40 \
  --max-num-seqs 16 \
  > "$UAI/logs/judge_8006.log" 2>&1 &
echo "judge-starting"
for i in $(seq 1 30); do
    sleep 5
    if curl -s --max-time 3 http://127.0.0.1:8006/v1/models | grep -q Qwen3; then
        echo "judge-8006-ready (waited $((i*5))s)"
        exit 0
    fi
done
echo "judge-8006-NOT-READY"
exit 1