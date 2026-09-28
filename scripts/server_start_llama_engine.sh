#!/usr/bin/env bash
# 启动 llama.cpp 引擎（8001，只绑回环），用**本地 GGUF**；并做一次最小推理形状验证
# 纪律：只打印状态/形状/布尔，不打印任何时延或精度数值。
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
MODELS="$UAI/models"

export CUDA_VISIBLE_DEVICES=0   # 用户 fish 里是 2（指向不存在的卡），必须覆盖
cd "$SRC" || exit 1
mkdir -p "$UAI/logs"

GGUF="$MODELS/qwen2.5-1.5b-instruct-gguf/qwen2.5-1.5b-instruct-q4_k_m.gguf"
if [ ! -f "$GGUF" ]; then
  echo "ERROR: 找不到本地 GGUF: $GGUF"
  exit 1
fi

pkill -f 'llama_cpp_server.py' && echo "llama.cpp stopped" || true
sleep 2

echo "===== start $(date '+%F %T') =====" >> "$UAI/logs/llama_cpp.log"
nohup "$UAI/venv/bin/python" scripts/llama_cpp_server.py \
  --model "$GGUF" --host 127.0.0.1 --port 8001 \
  --n_ctx 4096 --n_gpu_layers -1 \
  >> "$UAI/logs/llama_cpp.log" 2>&1 &
echo "llama.cpp pid $!"

echo "--- 等待就绪（GGUF 加载 + 层上 GPU）---"
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8001/health 2>/dev/null || true)
  if [ "$code" = "200" ]; then
    echo "ready (t≈$((i*2))s)"
    break
  fi
  sleep 2
done
echo "llama.cpp /health -> $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8001/health 2>/dev/null)"

echo "--- GPU offload 能力 ---"
"$UAI/venv/bin/python" -c "import llama_cpp; print('supports_gpu_offload =', llama_cpp.llama_supports_gpu_offload())"

echo "--- 最小推理（只报形状）---"
curl -s --max-time 180 -X POST http://127.0.0.1:8001/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"用一句话说明甲状腺结节随访的意义：","max_tokens":32,"temperature":0.1}' \
  | "$UAI/venv/bin/python" -c 'import json,sys
d=json.load(sys.stdin)
ch=d.get("choices") or [{}]
txt=(ch[0].get("text") or "")
print("choices 条数:", len(ch), "| 文本非空:", bool(txt.strip()), "| usage 键:", sorted((d.get("usage") or {}).keys()))'

echo "--- 尾部日志 ---"
tail -6 "$UAI/logs/llama_cpp.log" | tr -d '\000'
echo "=== done ==="
