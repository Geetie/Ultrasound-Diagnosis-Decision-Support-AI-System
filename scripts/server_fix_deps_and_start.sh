#!/usr/bin/env bash
# 扫描 app/ 的第三方导入 → 一次性补齐缺失依赖 → 重启 API → 健康检查。
#
# 背景：requirements.txt 不完整（例如 app/api/v1/imaging.py 需要 pydicom 但清单里没有），
# 全新环境启动必崩。逐个报错逐个装太低效，这里做一次全量扫描。
set -u
ROOT=/mnt/ee16/home/student03/uai
SRC=$ROOT/src
PY=$ROOT/venv/bin/python
ENVF=$ROOT/.env
LOG=$ROOT/logs/api.log
cd "$SRC" || exit 1

echo "=== A. 扫描 app/ 的第三方导入 ==="
MISSING=$("$PY" - <<'PY'
import ast
import importlib.util
import pathlib
import sys

mods: set[str] = set()
for path in pathlib.Path("app").rglob("*.py"):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module.split(".")[0])

local = {"app", "edge", "safety_gateway", "scripts", "tests", "alembic", "config"}
stdlib = set(sys.stdlib_module_names)
candidates = sorted(m for m in mods if m not in stdlib and m not in local)
missing = [m for m in candidates if importlib.util.find_spec(m) is None]
print(" ".join(missing))
PY
)
echo "第三方导入候选总数（含已装）与缺失清单如下："
echo "缺失: ${MISSING:-（无）}"

echo
echo "=== B. 安装缺失包（模块名→包名做必要映射）==="
if [ -n "${MISSING// /}" ]; then
  PKGS=""
  for m in $MISSING; do
    case "$m" in
      yaml) PKGS="$PKGS pyyaml" ;;
      jose) PKGS="$PKGS python-jose[cryptography]" ;;
      PIL) PKGS="$PKGS Pillow" ;;
      cv2) PKGS="$PKGS opencv-python-headless" ;;
      sklearn) PKGS="$PKGS scikit-learn" ;;
      dotenv) PKGS="$PKGS python-dotenv" ;;
      watchdog) PKGS="$PKGS watchdog" ;;
      wandb) PKGS="$PKGS wandb" ;;
      *) PKGS="$PKGS $m" ;;
    esac
  done
  echo "安装:$PKGS"
  "$PY" -m pip install --disable-pip-version-check $PKGS > /tmp/missdeps.log 2>&1
  echo "rc=$?"
  tail -4 /tmp/missdeps.log
else
  echo "无需安装"
fi

echo
echo "=== C. 重启 API ==="
pkill -f 'uvicorn app.main:app' 2>/dev/null || true
sleep 2
set -a; . "$ENVF"; set +a
nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 --log-level info > "$LOG" 2>&1 &
echo "pid=$!"
sleep 30

echo
echo "=== D. 启动日志尾部 ==="
tail -18 "$LOG"

echo
echo "=== E. 健康检查（真实路由为 /health、/health/hsm、/health/cache）==="
for path in /api/v1/health /api/v1/health/hsm /api/v1/health/cache /api/v1/health/ready; do
  code=$(curl -s -o /tmp/hc.out -m 10 -w '%{http_code}' "http://127.0.0.1:18000$path" 2>/dev/null)
  echo "$path -> http=$code  $(head -c 200 /tmp/hc.out 2>/dev/null)"
done

echo
echo "=== F. 监听与进程 ==="
ss -ltn 2>/dev/null | grep 18000 || echo "(未监听 18000)"
pgrep -af 'uvicorn app.main:app' | head -2
echo "FIXDEPS_DONE"
