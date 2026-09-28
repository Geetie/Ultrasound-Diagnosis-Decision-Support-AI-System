#!/usr/bin/env bash
# 阶段 A 诊断：定位应用 lifespan 卡点（只读）
set -u
UAI="$HOME/uai"

echo "===== 1) 相关进程 ====="
ps -eo pid,etime,rss,cmd | grep -aE 'uvicorn|safety_gateway|gunicorn' | grep -av grep || echo "no uvicorn/safety proc"

echo
echo "===== 2) 监听端口 ====="
ss -ltn 2>/dev/null | grep -aE ':(18000|18001|9001|9090|16379|15432|6333|6334)' || echo "none listening"

echo
echo "===== 3) health 探测 ====="
code=$(curl -s -o /tmp/h.txt -w '%{http_code}' --max-time 10 http://127.0.0.1:18000/api/v1/health 2>/dev/null || echo curl-fail)
echo "http=$code"
head -c 500 /tmp/h.txt 2>/dev/null; echo

echo
echo "===== 4) api.log 尾部（去 NUL，最后 120 行）====="
if [ -f "$UAI/logs/api.log" ]; then
  ls -l --time-style=+%F_%T "$UAI/logs/api.log"
  tr -d '\000' < "$UAI/logs/api.log" | tail -120
else
  echo "no api.log"
fi

echo
echo "===== 5) .env 键名（只列名不列值）====="
if [ -f "$UAI/.env" ]; then
  sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$UAI/.env" | sort | tr '\n' ' '
  echo
fi

echo
echo "===== 6) uvicorn 进程环境相关键（值已脱敏）====="
for p in $(pgrep -f uvicorn 2>/dev/null); do
  echo "-- pid $p"
  tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null \
    | grep -aE '^(APP_ENV|APP_PORT|REDIS_URL|DATABASE_URL|SAFETY_GATEWAY|QDRANT|DEPLOYMENT_PROFILE|INFERENCE_ENGINE|ENABLE_|USE_SOFT_HSM)' \
    | sed -E 's/(PASSWORD|KEY|SECRET)[^=]*=.*/\1=<redacted>/'
done

echo
echo "===== 7) 已装的启动期缺失包 ====="
"$UAI/venv/bin/pip" list 2>/dev/null | grep -aiE '^(pydicom|elasticsearch|gmssl|cachetools|jieba|onnxruntime|pynvml|qdrant|scipy|zstandard|pkuseg|transformers|torch) '

echo "===== diag done ====="
