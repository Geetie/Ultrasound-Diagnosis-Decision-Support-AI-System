#!/usr/bin/env bash
# 判官 GPU 化部署：Qwen3-8B-AWQ → vLLM 官方 server @ 8006
# 前置：~/.cache/huggingface/hub/models--Qwen--Qwen3-8B-AWQ 下载完成
set -u
UAI="$HOME/uai"
# 1) 杀旧判官（如有）
OLD=$(ss -ltnp 2>/dev/null | grep ':8006' | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "${OLD}" ] && { kill "${OLD}"; sleep 3; kill -9 "${OLD}" 2>/dev/null; }
# 2) 启动（CUDA 0 与主 vLLM 共卡；AWQ 4bit 约 6GB 显存 + KV）
cd "$UAI/venv-vllm" 2>/dev/null || true
export CUDA_VISIBLE_DEVICES=0
export VLLM_USE_FLASHINFER_SAMPLER=0
PATH="$UAI/venv-vllm/bin:$PATH" nohup "$UAI/venv-vllm/bin/python" -m vllm.entrypoints.openai.api_server \
  --model "$UAI/models/Qwen3-8B" \
  --served-model-name Qwen3-8B \
  --port 8006 --max-model-len 4096 \
  --gpu-memory-utilization 0.25 \
  --max-num-seqs 16 \
  > "$UAI/logs/judge_8006.log" 2>&1 &
echo "judge-starting"
