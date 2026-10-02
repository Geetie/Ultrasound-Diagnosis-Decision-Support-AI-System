#!/usr/bin/env bash
# 在**独立 venv** 里安装 vLLM。
#
# 为什么不装进主 venv：vLLM 会拉取/可能降级 torch，而主 venv（~/uai/venv）里的
# torch 2.14.0+cu130 与 transformers 正被**正在运行的**嵌入/重排服务（8004/8005）使用 ——
# 同 venv 升级/降级 torch 有把它们当场弄坏的风险。隔离安装互不影响，
# 且 scripts/vllm_server.py 只需要 vllm + fastapi/uvicorn 这几个依赖。
set -u
UAI="$HOME/uai"
VENV="$UAI/venv-vllm"

export CUDA_VISIBLE_DEVICES=0

if [ ! -x "$VENV/bin/python" ]; then
  echo "=== 创建独立 venv（--without-pip 绕开 ensurepip 缺失/PEP 668）==="
  python3 -m venv --without-pip "$VENV"
  curl -sS https://bootstrap.pypa.io/get-pip.py | "$VENV/bin/python"
fi
"$VENV/bin/python" -m pip --version

echo "=== 安装 vLLM 开始 $(date '+%F %T') ==="
"$VENV/bin/python" -m pip install --upgrade pip 2>&1 | tail -2
"$VENV/bin/python" -m pip install vllm 2>&1 | tail -30
echo "=== 安装结束 $(date '+%F %T') ==="

echo "--- 自检 ---"
"$VENV/bin/python" - <<'PY' 2>&1 | tail -4
try:
    import torch
    import vllm

    print("vllm =", vllm.__version__)
    print("torch =", torch.__version__)
    print("cuda_available =", torch.cuda.is_available())
except Exception as exc:  # noqa: BLE001
    print("IMPORT_FAILED:", type(exc).__name__, exc)
PY
echo "=== done ==="
