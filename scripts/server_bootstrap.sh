#!/usr/bin/env bash
# 学校服务器（student03@10.20.28.21）无 sudo 初始化脚本。
#
# 为什么是"宿主机 venv"而不是 Docker：
#   1. 该机 Docker Hub 不可达（registry-1.docker.io 连接被拒），
#      /etc/docker-student03/daemon.json 里配的 4 个 registry-mirrors 实测未生效；
#   2. daemon 启用了 userns-remap，与 GPU 直通是已知冲突组合；
#   3. 系统无 pip、且很可能是无 sudo 普通用户 —— venv + 自举 pip 恰好绕开这两点。
#
# 幂等：可重复执行；已存在的 venv/已装的包不会重复处理。
# 用法：bash server_bootstrap.sh

set -u
ROOT=/mnt/ee16/home/student03/uai

echo "=== 0. 工作目录 ==="
mkdir -p "$ROOT"/logs "$ROOT"/models "$ROOT"/data "$ROOT"/reports
ls -ld "$ROOT" "$ROOT"/logs "$ROOT"/models "$ROOT"/data "$ROOT"/reports 2>/dev/null
df -h /mnt/ee16 | tail -1

echo
echo "=== 1. pip 自举（用户级，不需要 sudo）==="
if python3 -m pip --version >/dev/null 2>&1; then
  echo "系统 pip 已可用：$(python3 -m pip --version | head -1)"
else
  echo "系统无 pip → 取 get-pip.py 做 --user 安装"
  cd /tmp || exit 1
  if curl -fsSL -o get-pip.py https://bootstrap.pypa.io/get-pip.py; then
    python3 get-pip.py --user --no-warn-script-location 2>&1 | tail -3
  else
    echo "get-pip.py 下载失败（bootstrap.pypa.io 不可达）"
  fi
fi
python3 -m pip --version 2>&1 | head -1

echo
echo "=== 2. 创建 venv ==="
if [ -x "$ROOT/venv/bin/python" ]; then
  echo "venv 已存在"
else
  if python3 -m venv "$ROOT/venv" 2>/dev/null; then
    echo "venv 创建成功"
  else
    echo "标准 venv 失败（Ubuntu 常缺 ensurepip）→ 用 --without-pip + 注入 pip"
    python3 -m venv --without-pip "$ROOT/venv" || { echo "venv 仍失败，终止"; exit 1; }
    curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$ROOT/venv/bin/python" - 2>&1 | tail -3
  fi
fi
"$ROOT/venv/bin/python" -V
"$ROOT/venv/bin/python" -m pip --version 2>&1 | head -1

echo
echo "=== 3. 后台安装 GPU 版 torch（PyPI 直装，实测 pypi.org 可达）==="
LOG="$ROOT/logs/torch_install.log"
if "$ROOT/venv/bin/python" -c 'import torch' 2>/dev/null; then
  "$ROOT/venv/bin/python" - <<'PY'
import torch
print("torch 已安装:", torch.__version__, "cuda_available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))
PY
else
  nohup "$ROOT/venv/bin/python" -m pip install --no-input --disable-pip-version-check torch \
    > "$LOG" 2>&1 &
  echo "已后台启动 torch 安装 pid=$!  日志=$LOG"
fi

echo
echo "=== 4. 收尾快照 ==="
"$ROOT/venv/bin/python" -m pip list 2>/dev/null | head -10
df -h /mnt/ee16 | tail -1
echo "BOOTSTRAP_DONE"
