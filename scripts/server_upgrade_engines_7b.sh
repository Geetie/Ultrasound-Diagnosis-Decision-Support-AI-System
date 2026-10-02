#!/usr/bin/env bash
# A3：把 8001/8002 的基座从 Qwen2.5-1.5B 升级到 Qwen2.5-7B（同量化等级对比）
#   8001 llama.cpp：GGUF Q4_K_M（与 1.5B 的 Q4_K_M 同级）
#   8002 vLLM：    fp16（与 1.5B 的 fp16 同级）；gpu-memory-utilization 0.3→0.4
# 显存预算（48GB）：llama 7B Q4 ≈5.5GB + vLLM 0.4≈19.2GB + 嵌入三服务 ≈4GB ≈ 29GB，无压力
# 用法：bash ~/server_upgrade_engines_7b.sh   （幂等，完成后两引擎 /health=200）
set -u
UAI="$HOME/uai"

export CUDA_VISIBLE_DEVICES=0
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1

GGUF="$UAI/models/qwen2.5-7b-instruct-gguf/qwen2.5-7b-instruct-q4_k_m.gguf"
HF7B="$UAI/models/Qwen2.5-7B-Instruct"
if [ ! -f "$GGUF" ]; then
  # GGUF 分片形态（snapshot_download 会落多分片 + 合并名，两种都试）
  FIRST_GGUF=$(ls "$UAI/models/qwen2.5-7b-instruct-gguf"/*q4_k_m*.gguf 2>/dev/null | head -1)
  if [ -n "$FIRST_GGUF" ]; then GGUF="$FIRST_GGUF"; else echo "ERROR: 7B GGUF 未就位"; exit 1; fi
fi
if [ ! -d "$HF7B" ]; then echo "ERROR: 7B HF 权重未就位"; exit 1; fi

echo "=== 重启 8001 llama.cpp（7B Q4_K_M）==="
pkill -f 'llama_cpp_server.py' 2>/dev/null || true
sleep 2
cd "$UAI/src"
echo "===== start 7b $(date '+%F %T') =====" >> "$UAI/logs/llama_cpp.log"
nohup "$UAI/venv/bin/python" scripts/llama_cpp_server.py \
  --model "$GGUF" --host 127.0.0.1 --port 8001 \
  --n_ctx 4096 --n_gpu_layers -1 \
  >> "$UAI/logs/llama_cpp.log" 2>&1 &
for i in $(seq 1 90); do
  sleep 2
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8001/health 2>/dev/null || true)
  [ "$code" = "200" ] && { echo "8001 ready (t≈$((i*2))s)"; break; }
done
echo "8001 /health -> $code"

echo "=== 重启 8002 vLLM（7B fp16, gpu_mem_util=0.4）==="
pkill -f 'vllm_server.py' 2>/dev/null || true
sleep 2
export PATH="$UAI/venv-vllm/bin:$PATH"
export VLLM_USE_FLASHINFER_SAMPLER=0
echo "===== start 7b $(date '+%F %T') =====" >> "$UAI/logs/vllm.log"
nohup "$UAI/venv-vllm/bin/python" scripts/vllm_server.py \
  --model "$HF7B" --host 127.0.0.1 --port 8002 \
  --device cuda --gpu-memory-utilization 0.4 \
  >> "$UAI/logs/vllm.log" 2>&1 &
for i in $(seq 1 150); do
  sleep 4
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8002/health 2>/dev/null || true)
  [ "$code" = "200" ] && { echo "8002 ready (t≈$((i*4))s)"; break; }
  if ! pgrep -f 'vllm_server.py' > /dev/null; then
    echo "!! vllm 进程已退出："; tail -20 "$UAI/logs/vllm.log" | tr -d '\000'; exit 2
  fi
done
echo "8002 /health -> $code"

echo "=== 双引擎 chat 冒烟（A2 统一形态）==="
curl -s --max-time 180 -X POST http://127.0.0.1:8001/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"甲状腺结节，请给出TI-RADS分级。"}],"max_tokens":64,"temperature":0}' \
  | "$UAI/venv/bin/python" -c 'import json,sys; d=json.load(sys.stdin); print("8001:", repr((d["choices"][0]["message"]["content"] or "")[:80]))'
curl -s --max-time 180 -X POST http://127.0.0.1:8002/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"Qwen2.5-7B-Instruct","messages":[{"role":"user","content":"甲状腺结节，请给出TI-RADS分级。"}],"max_tokens":64,"temperature":0}' \
  | "$UAI/venv-vllm/bin/python" -c 'import json,sys; d=json.load(sys.stdin); print("8002:", repr((d["choices"][0]["message"]["content"] or "")[:80]))'
echo "=== done ==="
