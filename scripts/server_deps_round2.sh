#!/usr/bin/env bash
# 第二轮补齐依赖：先把能装的 10 个装完（避开 pkuseg 拖垮整批），再单独处理 pkuseg，然后重启 API。
#
# 为什么拆开：`pip install a b c` 中只要有一个无法构建，**整批都不装**（上次就栽在 pkuseg），
# 于是 pydicom 等明明有 wheel 的包也没装上，误判为"网络/源问题"。
set -u
ROOT=/mnt/ee16/home/student03/uai
SRC=$ROOT/src
PY=$ROOT/venv/bin/python
ENVF=$ROOT/.env
LOG=$ROOT/logs/api.log
cd "$SRC" || exit 1

echo "=== A. 先确认 pkuseg 是启动期导入还是延迟导入（决定它是否阻塞启动）==="
grep -rn 'import pkuseg\|pkuseg' "$SRC/app" 2>/dev/null | head -6 || echo "(app 内无 pkuseg 引用)"

echo
echo "=== B. 装 10 个有轮子的包（不含 pkuseg）==="
PKGS="cachetools elasticsearch gmssl jieba onnxruntime pydicom pynvml qdrant_client scipy zstandard"
"$PY" -m pip install --disable-pip-version-check $PKGS > /tmp/deps_r2.log 2>&1
echo "rc=$?"
tail -3 /tmp/deps_r2.log
echo "--- 校验导入 ---"
"$PY" -c 'import importlib
for m in "cachetools elasticsearch gmssl jieba onnxruntime pydicom pynvml qdrant_client scipy zstandard".split():
    try:
        importlib.import_module(m); print("  ok  ", m)
    except Exception as e:
        print("  FAIL", m, type(e).__name__, str(e)[:70])'

echo
echo "=== C. 单独试 pkuseg（无 wheel，best-effort）==="
"$PY" -m pip install --disable-pip-version-check --no-build-isolation pkuseg > /tmp/pkuseg.log 2>&1
echo "pkuseg_rc=$?"
tail -3 /tmp/pkuseg.log

echo
echo "=== D. 重启 API ==="
pkill -f 'uvicorn app.main:app' 2>/dev/null || true
sleep 2
set -a; . "$ENVF"; set +a
nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 --log-level info > "$LOG" 2>&1 &
echo "pid=$!"
sleep 35

echo
echo "=== E. 启动日志尾部 ==="
tail -20 "$LOG"

echo
echo "=== F. 健康检查 ==="
for path in /api/v1/health /api/v1/health/hsm /api/v1/health/cache; do
  code=$(curl -s -o /tmp/hc.out -m 10 -w '%{http_code}' "http://127.0.0.1:18000$path" 2>/dev/null)
  echo "$path -> http=$code  $(head -c 220 /tmp/hc.out 2>/dev/null)"
done

echo
echo "=== G. 监听与进程 ==="
ss -ltn 2>/dev/null | grep 18000 || echo "(未监听 18000)"
pgrep -af 'uvicorn app.main:app' | head -2
echo "DEPS2_DONE"
