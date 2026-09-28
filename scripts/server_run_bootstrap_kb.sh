#!/usr/bin/env bash
# 运行知识库冒烟基线（自造条目，非临床依据）。必须由 bash 脚本 source .env ——
# 远端默认 shell 是 fish，内联 `. .env` 会被 fish 当成自己的语法吃掉。
set -u
UAI="$HOME/uai"
cd "$UAI/src" || exit 1
set -a
# shellcheck disable=SC1091
. "$UAI/.env"
set +a

"$UAI/venv/bin/python" "$HOME/server_bootstrap_kb_smoke.py"
