#!/usr/bin/env bash
# 补齐启动强校验所需的密钥（本地生成、不回显）→ 重启 API → 健康检查。
#
# 依据（app/config/settings.py:499-508 的生产强校验 + key_manager.py:385-447）：
#   * HSM_PRIVATE_KEY_PASSWORD  必填（否则启动即拒）
#   * HSM_KEY_ENCRYPTION_KEY    必填，且**必须是合法 Fernet key**
#   * 无真机 HSM 时按设计回退软 HSM：USE_SOFT_HSM=true
# JWT 签名密钥由 auth.py 在 HSM 内自动生成，无需环境变量。
set -u
ROOT=/mnt/ee16/home/student03/uai
SRC=$ROOT/src
PY=$ROOT/venv/bin/python
ENVF=$ROOT/.env
LOG=$ROOT/logs/api.log

echo "=== A. 补密钥（幂等，值不回显）==="
add_if_missing() {
  key=$1; val=$2
  if grep -q "^${key}=" "$ENVF"; then
    echo "  已存在: $key"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENVF"
    echo "  已写入: $key"
  fi
}
add_if_missing HSM_PRIVATE_KEY_PASSWORD "$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | base64 | tr -d '/+=')"
add_if_missing HSM_KEY_ENCRYPTION_KEY "$("$PY" -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
add_if_missing USE_SOFT_HSM "true"
chmod 600 "$ENVF"
echo "  现有键: $(grep -o '^[A-Z_]*' "$ENVF" | tr '\n' ' ')"

echo
echo "=== B. 健康路由的真实路径（避免猜错端点）==="
grep -rn 'router.get\|@router' "$SRC/app/api/v1/health.py" 2>/dev/null | head -8 || echo "(无 app/api/v1/health.py)"

echo
echo "=== C. 重启 API（加载 .env 全部变量）==="
pkill -f 'uvicorn app.main:app' 2>/dev/null || true
sleep 2
cd "$SRC" || exit 1
set -a; . "$ENVF"; set +a
nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 --log-level info > "$LOG" 2>&1 &
echo "pid=$!  日志=$LOG"
sleep 30

echo
echo "=== D. 启动日志尾部 ==="
tail -20 "$LOG"

echo
echo "=== E. 健康检查 ==="
for path in /api/v1/health /api/v1/health/ready /api/v1/health/live /health /docs; do
  code=$(curl -s -o /tmp/hc.out -m 8 -w '%{http_code}' "http://127.0.0.1:18000$path" 2>/dev/null)
  echo "$path -> http=$code  $(head -c 140 /tmp/hc.out 2>/dev/null)"
done

echo
echo "=== F. 监听与进程 ==="
ss -ltn 2>/dev/null | grep 18000 || echo "(未监听 18000)"
pgrep -af 'uvicorn app.main:app' | head -2
echo "SECRETS_API_DONE"
