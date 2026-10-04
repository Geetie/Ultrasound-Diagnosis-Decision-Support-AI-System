#!/usr/bin/env bash
# 按 SKILL 约定把 8002 换回官方 OpenAI server（Qwen2.5-7B-Instruct）
# 为什么要换：自写 vllm_server.py 包装**没有 continuous batching**，
# 并发实为串行（skill 记录：GPU 100% 但吞吐 61 tok/s）；且缺 /v1/models
# 等标准端点。交付前应回到项目既定形态。
set -u
UAI="$HOME/uai"
say() { echo "[$(date +%H:%M:%S)] $*"; }

say "=== 0) 7B 权重是否在位 ==="
if [ ! -d "$UAI/models/Qwen2.5-7B-Instruct" ]; then
  say "  MISSING: $UAI/models/Qwen2.5-7B-Instruct"
  ls -d "$UAI/models/"*Qwen2.5* 2>/dev/null | sed 's/^/  /'
  exit 2
fi
say "  ok: $(du -sh "$UAI/models/Qwen2.5-7B-Instruct" | cut -f1)"

say "=== 1) 停 8002 自写包装 + 残留 EngineCore ==="
pkill -9 -f "vllm_server.py" 2>/dev/null || true
sleep 3
pkill -9 -f "VLLM::EngineCore" 2>/dev/null || true
sleep 3
say "  显存: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"

say "=== 2) 起官方 OpenAI server（三环境变量缺一即崩，见 skill）==="
export CUDA_VISIBLE_DEVICES=0
export PATH="$UAI/venv-vllm/bin:$PATH"
export VLLM_USE_FLASHINFER_SAMPLER=0
cd "$UAI/src" || exit 1
setsid nohup "$UAI/venv-vllm/bin/python" -m vllm.entrypoints.openai.api_server \
  --model "$UAI/models/Qwen2.5-7B-Instruct" --served-model-name Qwen2.5-7B-Instruct \
  --host 127.0.0.1 --port 8002 --gpu-memory-utilization 0.46 \
  --max-num-batched-tokens 16384 > "$UAI/logs/vllm.log" 2>&1 < /dev/null &
disown

say "=== 3) 等就绪（7B 比 1.5B 慢，最多 5 分钟）==="
for i in $(seq 1 100); do
  sleep 3
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8002/health 2>/dev/null)
  if [ "$c" = "200" ]; then
    say "  8002 OK ($((i*3))s)"
    break
  fi
  if [ $((i % 10)) -eq 0 ]; then
    say "  ... $((i*3))s mem=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
  fi
done

say "=== 4) 验标准端点 ==="
echo "  /v1/models:"
curl -s --max-time 20 http://127.0.0.1:8002/v1/models | head -c 300; echo ""
echo "  chat:"
curl -s --max-time 90 -X POST http://127.0.0.1:8002/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen2.5-7B-Instruct","messages":[{"role":"user","content":"用一句话说明甲状腺低回声结节的临床意义"}],"max_tokens":60}' \
  | head -c 400; echo ""
say "  显存: $(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
