#!/usr/bin/env bash
# 在**独立 venv** 里安装 vLLM。
#
# 为什么不装进主 venv：vLLM 会拉取/可能降级 torch，而主 venv（~/uai/venv）里的
# torch 2.14.0+cu130 与 transformers 正被**正在运行的**嵌入/重排服务（8004/8005）使用 ——
# 同 venv 升级/降级 torch 有把它们当场弄坏的风险。隔离安装互不影响，
# 且 scripts/vllm_server.py 只需要 vllm + fastapi/uvicorn 这几个依赖。
#
# ## A.95 Q33：**版本必须钉死**（15 号 P1 第 5 项）
#
# 原实现是 `pip install vllm` —— 无版本约束，每次重装可能拿到不同版本，
# 这违反仓库「27 项精确锁定」的约定，也意味着：
# * 推理行为（采样/结构化输出/显存占用）可能静默变化；
# * 无法复现历史评测（"上个月 AUC 0.89"可能是另一个 vLLM 跑的）。
#
# 真机实测版本：**0.30.0**（`~/uai/venv-vllm`）。
# 用法：
#   ./server_install_vllm_isolated.sh              # 装 VLLM_VERSION（默认 0.30.0）
#   VLLM_VERSION=0.30.1 ./server_install_vllm_isolated.sh   # 显式升级
set -u
UAI="$HOME/uai"
VENV="$UAI/venv-vllm"

#: 钉死的 vLLM 版本（与真机实装一致）。改这里之前先读 15 号 P1 的验收判据。
VLLM_VERSION="${VLLM_VERSION:-0.30.0}"

export CUDA_VISIBLE_DEVICES=0

if [ ! -x "$VENV/bin/python" ]; then
  echo "=== 创建独立 venv（--without-pip 绕开 ensurepip 缺失/PEP 668）==="
  python3 -m venv --without-pip "$VENV"
  curl -sS https://bootstrap.pypa.io/get-pip.py | "$VENV/bin/python"
fi
"$VENV/bin/python" -m pip --version

echo "=== 安装 vLLM==$VLLM_VERSION 开始 $(date '+%F %T') ==="
"$VENV/bin/python" -m pip install --upgrade pip 2>&1 | tail -2
# **精确版本**：不用 `>=`/无版本，否则装出来的东西不可复现
"$VENV/bin/python" -m pip install "vllm==$VLLM_VERSION" 2>&1 | tail -30
echo "=== 安装结束 $(date '+%F %T') ==="

echo "--- 自检（版本必须与钉子一致）---"
EXPECTED="$VLLM_VERSION" "$VENV/bin/python" - <<'PY' 2>&1 | tail -6
import os
import sys

try:
    import torch
    import vllm

    want = os.environ.get("EXPECTED", "")
    got = vllm.__version__
    print("vllm =", got, "(expected", want + ")")
    print("torch =", torch.__version__)
    print("cuda_available =", torch.cuda.is_available())
    if want and got != want:
        print(f"MISMATCH: 装到 {got}，期望 {want} —— 依赖解析把版本改掉了")
        sys.exit(1)
except SystemExit:
    raise
except Exception as exc:  # noqa: BLE001
    print("IMPORT_FAILED:", type(exc).__name__, exc)
    sys.exit(1)
PY
echo "=== done ==="
