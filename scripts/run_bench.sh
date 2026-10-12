#!/usr/bin/env bash
# 容量压测启动器（A.53）。
#
# 为什么需要它：`bench_concurrency_staircase.py` 的 `ensure_users()` 为了建压测账号会
# import `app.security.auth`（HSM 是模块级单例）——若环境里没有
# `HSM_KEY_ENCRYPTION_KEY`，会直接 `RuntimeError: ... Refusing to use hardcoded
# encryption key`（2026-10-12 实测）。所以必须以**与主服务同源的环境**跑。
#
# 用法（服务器，仓库根 ~/uai/src）：
#   bash scripts/run_bench.sh --levels 1,5,10,20,50 --users 10 --cooldown 20 \
#        --poll-deadline 240 --output reports/prod_capacity_a53.json
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

ENV_FILE="$ROOT/../.env"   # ~/uai/.env（主服务同源）
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

export PYTHONPATH="$ROOT"
echo "[launcher] env=$ENV_FILE | HSM_KEY=$([ -n "${HSM_KEY_ENCRYPTION_KEY:-}" ] && echo loaded || echo MISSING)"

exec "${UAI_VENV_PYTHON:-$ROOT/../venv/bin/python}" scripts/bench_concurrency_staircase.py "$@"
