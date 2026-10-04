#!/usr/bin/env bash
# 唯一权威的**裸机**主服务启动脚本（学校 GPU 服务器 18000）。
#
# 为什么必须有这个文件（不是"再写一个方便脚本"）：
#   2026-10-05 真机实测 `ss -ltn` 得到 `0.0.0.0:18000` —— 临床 API + 真实患者
#   检查链路对整个校园网裸奔，直接违反 AGENTS.md 硬规则 #3「服务只绑
#   127.0.0.1」。根因不是有人写错了，而是**没有权威启动入口**：仓库里
#   `_sync_apply.sh` / `_restart_main_std.sh` 写 `--host 127.0.0.1`，另有 7 个
#   `deploy_*.sh` 写死 `--host 0.0.0.0`，谁最后跑谁说了算，改绑必然漂回。
#   本文件把"裸机 = loopback"变成唯一默认，并被 tests/unit/config/
#   test_api_host_binding.py 断言锁住。
#
# 容器不受影响：Dockerfile.dev 硬编码 `--host 0.0.0.0`（容器内必须为
# 0.0.0.0，否则 `ports: 8000:8000` 映射不通），与本脚本无关。
#
# 用法：
#   bash ~/uai/src/scripts/start_main.sh            # 前台等待健康后退出
#   bash ~/uai/src/scripts/start_main.sh --no-restart  # 只探活，不重启
#
# 跨机访问（本机浏览器验证的唯一姿势）：
#   ssh -N -L 8000:127.0.0.1:18000 student03@10.20.28.21
#   浏览器打开 http://127.0.0.1:8000/ui/
set -uo pipefail

UAI="${UAI:-$HOME/uai}"
SRC="$UAI/src"
PORT="${API_PORT:-18000}"
# 铁律：服务器 CUDA_VISIBLE_DEVICES=2 是无效值（该机可见 GPU 从 0 起算），
# 分割推理必须显式置 0，否则 NoduleSegmenter 拿不到设备。
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export PYTHONPATH="$SRC${PYTHONPATH:+:$PYTHONPATH}"
# 段落轮廓进报告 + 属性提示（2026-10-05 交付形态）
export NODULE_CONTOUR_IN_PIPELINE_ENABLED="${NODULE_CONTOUR_IN_PIPELINE_ENABLED:-1}"
export ATTRIBUTE_HINTS_ENABLED="${ATTRIBUTE_HINTS_ENABLED:-1}"

PY="$UAI/venv/bin/python"
LOG="$UAI/logs/api.log"
PROBE="http://127.0.0.1:$PORT/api/v1/health"

say() { echo "[$(date '+%H:%M:%S')] $*"; }

# 本脚本起始时刻，用于末尾判定"进程是不是真换过"（见健康探测段）
T0=$(date +%s)

[ -x "$PY" ] || { say "FATAL: 找不到 $PY"; exit 1; }
[ -d "$SRC" ] || { say "FATAL: 找不到 $SRC"; exit 1; }
mkdir -p "$UAI/logs"

# ---------------------------------------------------------------- 探活模式
if [ "${1:-}" = "--no-restart" ]; then
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 "$PROBE" 2>/dev/null || true)
  if [ "$code" = "200" ]; then
    say "api-alive(200) pid=$(pgrep -f "uvicorn app.main:app" | head -1)"
    exit 0
  fi
  say "api-down(code=$code)"
  exit 1
fi

# ---------------------------------------------------------------- 停旧进程
# 模式串只匹配 uvicorn 本身，**不含本脚本名** —— 否则 pkill -f 会把
# `ssh ... "bash start_main.sh"` 这条远程命令行一起杀掉（静默自杀，
# 重启没执行却回报成功，是本项目反复踩的坑）。
OLD=$(pgrep -f "uvicorn app.main:app" | tr '\n' ' ')
if [ -n "${OLD// /}" ]; then
  say "停旧进程: $OLD"
  # shellcheck disable=SC2086
  kill $OLD 2>/dev/null
  for _ in $(seq 1 10); do
    pgrep -f "uvicorn app.main:app" >/dev/null || break
    sleep 1
  done
  pgrep -f "uvicorn app.main:app" >/dev/null && {
    # shellcheck disable=SC2086
    kill -9 $OLD 2>/dev/null
    sleep 2
  }
fi
pgrep -f "uvicorn app.main:app" >/dev/null && { say "FATAL: 旧进程杀不掉"; exit 1; }

# ---------------------------------------------------------------- 起新进程
cd "$SRC" || exit 1
if [ -f "$UAI/.env" ]; then
  set -a; . "$UAI/.env"; set +a
fi
setsid nohup "$PY" -m uvicorn app.main:app \
  --host 127.0.0.1 --port "$PORT" --log-level warning \
  > "$LOG" 2>&1 < /dev/null &
disown
say "已拉起，等待健康..."

# ---------------------------------------------------------------- 健康探测
for i in $(seq 1 40); do
  sleep 3
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 "$PROBE" 2>/dev/null || true)
  if [ "$code" = "200" ]; then
    PID=$(pgrep -f "uvicorn app.main:app" | head -1)
    # 铁律：探活 200 可能是**旧进程假活**。核对新进程确实比本脚本启动还晚。
    #
    # 用 `ps -o etimes=`（已运行秒数），不要用 `ps -o lstart=` + `date -d`：
    # 后者在真机实测静默解析失败返回 0，age 变成"当前 epoch 秒"（1.79e9），
    # 每次启动都误报"疑似未真正重启"（2026-10-05 踩到，已修）。
    NOW=$(date +%s)
    AGE=$(ps -o etimes= -p "$PID" 2>/dev/null | tr -d ' ')
    BIND=$(ss -ltn 2>/dev/null | awk -v p=":$PORT\$" '$4 ~ p {print $4}' | head -1)
    say "api-ready($((i*3))s) pid=$PID age=${AGE:-?}s (本次脚本已跑 $((NOW-T0))s) bind=${BIND:-unknown}"
    case "$BIND" in
      127.0.0.1:*) say "绑定合规（loopback）" ;;
      "")           say "WARN: 读不到监听地址，ss 不可用？" ;;
      *)            say "FATAL: 绑定不是 loopback（$BIND），违反硬规则 #3"; exit 1 ;;
    esac
    # AGE 明显大于脚本自身运行时长 → 该进程在脚本跑之前就存在 = 没真正重启
    if [ -n "${AGE:-}" ] && [ "$AGE" -gt $(( NOW - T0 + 30 )) ]; then
      say "FATAL: 进程已存在 ${AGE}s 而本次脚本仅跑 $((NOW-T0))s，判定为旧进程假活"
      exit 1
    fi
    exit 0
  fi
done

say "FATAL: 120s 未就绪，日志尾部："
tail -20 "$LOG"
exit 1
