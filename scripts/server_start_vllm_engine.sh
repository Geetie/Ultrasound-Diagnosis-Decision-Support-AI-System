#!/usr/bin/env bash
# 启动 vLLM 引擎（8002，只绑回环），用**独立 venv** ~/uai/venv-vllm。
# 纪律：只打印状态/形状/布尔，不打印任何时延或精度数值。
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
MODELS="$UAI/models"
VLLM_PY="$UAI/venv-vllm/bin/python"

export CUDA_VISIBLE_DEVICES=0   # 用户 fish 里是 2（指向不存在的卡），必须覆盖
cd "$SRC" || exit 1
mkdir -p "$UAI/logs"

# vLLM 0.30 的采样默认走 flashinfer，而 flashinfer 会在**首次调用时 JIT 编译**，
# 它调用的是 PATH 上的 `ninja` **可执行文件** —— 本机无 sudo、装不了 apt 的
# ninja-build，只有 venv 里的 python 包 → 真机报：
#   FileNotFoundError: [Errno 2] No such file or directory: 'ninja'
# （报错点在 vllm/v1/worker/gpu/sample/sampler.py → flashinfer/jit/cpp_ext.py run_ninja）
# 两处处置：
#   1) 把 venv 的 bin 放进 PATH（pip 的 ninja 包自带可执行文件）；
#   2) 关掉 flashinfer 采样器，退到 vLLM 原生 torch 采样 —— 少一个 JIT 环节；
#      本机 nvcc 是 12.4 而 torch 是 cu130，JIT 编译链本就错配，不值得依赖它。
export PATH="$UAI/venv-vllm/bin:$PATH"
export VLLM_USE_FLASHINFER_SAMPLER=0

MODEL_DIR="$MODELS/Qwen2.5-1.5B-Instruct"
if [ ! -d "$MODEL_DIR" ]; then
  echo "ERROR: 找不到 HF 权重目录: $MODEL_DIR"
  exit 1
fi
if [ ! -x "$VLLM_PY" ]; then
  echo "ERROR: 独立 venv 不存在: $VLLM_PY（先跑 server_install_vllm_isolated.sh）"
  exit 1
fi

pkill -f 'vllm_server.py' && echo "vllm stopped" || true
sleep 2

echo "===== start $(date '+%F %T') =====" >> "$UAI/logs/vllm.log"
nohup "$VLLM_PY" scripts/vllm_server.py \
  --model "$MODEL_DIR" --host 127.0.0.1 --port 8002 \
  --device cuda --gpu-memory-utilization 0.3 \
  >> "$UAI/logs/vllm.log" 2>&1 &
echo "vllm pid $!"

echo "--- 等待就绪（vLLM 引擎初始化可能较慢，最多等 10 分钟）---"
for i in $(seq 1 150); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8002/health 2>/dev/null || true)
  if [ "$code" = "200" ]; then
    echo "ready (t≈$((i*4))s)"
    break
  fi
  if ! pgrep -f 'vllm_server.py' > /dev/null; then
    echo "!! 进程已退出，尾部日志："
    tail -20 "$UAI/logs/vllm.log" | tr -d '\000'
    exit 2
  fi
  sleep 4
done
echo "vllm /health -> $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8002/health 2>/dev/null)"

echo "--- 最小推理（只报形状）---"
curl -s --max-time 180 -X POST http://127.0.0.1:8002/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"用一句话说明甲状腺结节随访的意义"}],"max_tokens":32,"temperature":0.1}' \
  | "$VLLM_PY" -c 'import json,sys
d=json.load(sys.stdin)
ch=d.get("choices") or [{}]
msg=(ch[0].get("message") or {})
txt=(msg.get("content") or "")
print("object:", d.get("object"), "| choices 条数:", len(ch), "| 文本非空:", bool(txt.strip()), "| usage 键:", sorted((d.get("usage") or {}).keys()))'

echo "--- 尾部日志 ---"
tail -8 "$UAI/logs/vllm.log" | tr -d '\000'
echo "=== done ==="
