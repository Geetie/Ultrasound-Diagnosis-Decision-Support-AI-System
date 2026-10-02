#!/bin/bash
# 0A 重测 v2：改用 --structured-output（两引擎追加完全相同任务指令），修 guided-json 退化
set -u
UAI="$HOME/uai"
SRC="$UAI/src"

echo "=== A. 停判官腾显存（llama-server 已在跑）==="
pkill -9 -f 'VLLM::EngineCore' 2>/dev/null
pkill -9 -f 'vllm.entrypoints.openai.api_server' 2>/dev/null
sleep 5
nvidia-smi --query-gpu=memory.used --format=csv,noheader

echo "=== B. vLLM 1.5B（8007）==="
export CUDA_VISIBLE_DEVICES=0
export VLLM_USE_FLASHINFER_SAMPLER=0
nohup "$UAI/venv-vllm/bin/python" -m vllm.entrypoints.openai.api_server \
  --model "$UAI/models/Qwen2.5-1.5B-Instruct" \
  --served-model-name Qwen2.5-1.5B-Instruct \
  --host 127.0.0.1 --port 8007 \
  --gpu-memory-utilization 0.12 \
  --max-model-len 4096 \
  > "$UAI/logs/vllm_8007.log" 2>&1 &
for i in $(seq 1 40); do
  curl -s --max-time 3 http://127.0.0.1:8007/v1/models | grep -q Qwen2.5 && break
  sleep 5
done
curl -s --max-time 3 http://127.0.0.1:8007/v1/models | grep -q Qwen2.5 || { echo "vllm8007 NOT READY"; exit 1; }
echo "vllm 8007 ready"

echo "=== C. 双引擎验证 v2（structured-output 口径，780 例）==="
cd "$SRC"
export PYTHONPATH="$SRC"
"$UAI/venv/bin/python" scripts/consistency_verify.py \
  --cases "$SRC/data/consistency_test_cases.json" \
  --llama-cpp-host 127.0.0.1 --llama-cpp-port 8001 \
  --vllm-host 127.0.0.1 --vllm-port 8007 --vllm-model Qwen2.5-1.5B-Instruct \
  --use-chat --structured-output --concurrency 8 --timeout 60 \
  --output "$SRC/reports/phase0a_retest_v2.json" 2>&1 | tail -25
RC=$?
echo "verify rc=$RC"

echo "=== D. 清理 + 恢复双 vLLM ==="
pkill -9 -f 'llama-server' 2>/dev/null
pkill -9 -f 'VLLM::EngineCore' 2>/dev/null
pkill -9 -f 'vllm.entrypoints.openai.api_server' 2>/dev/null
sleep 4
nohup bash "$SRC/scripts/_deploy_dual_vllm.sh" > "$UAI/logs/dual_vllm_start.log" 2>&1 &
echo "恢复中"
exit $RC
