#!/bin/bash
# 正式同步：HEAD 铺 ~/uai/src（保留服务器运行时文件），重启主服务
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
STAGE="/tmp/sync_stage"

echo "=== 1. 备份当前 src 关键运行时（.env 已天然不在 archive 中）==="
cp "$SRC/.env" "/tmp/sync_backup.env"
ls "$SRC/.env" "$SRC/.env.bak_fusion" > /dev/null && echo "env 文件在位"

echo "=== 2. 铺 HEAD（排除运行时状态文件）==="
cd "$STAGE"
tar -cf - --exclude='soft_hsm_enabled_at.txt' . | tar -xf - -C "$SRC"
echo "extracted"

echo "=== 3. 复核 .env 未被动 ==="
diff -q /tmp/sync_backup.env "$SRC/.env" && echo "env OK"
grep -c 'FUSION_LAYER_PORT' "$SRC/.env"

echo "=== 4. 复核差异收敛 ==="
REAL=0
while IFS= read -r line; do
  case "$line" in
    Files*)
      f=$(echo "$line" | sed "s|^Files $STAGE/||; s| and .*||")
      case "$f" in
        soft_hsm_enabled_at.txt) continue ;;
      esac
      if ! diff -q <(tr -d '\r' < "$STAGE/$f") <(tr -d '\r' < "$SRC/$f") >/dev/null 2>&1; then
        REAL=$((REAL+1)); echo "STILL DIFF: $f"
      fi
      ;;
  esac
done < <(LC_ALL=C diff -rq "$STAGE" "$SRC" \
  -x '.git' -x 'node_modules' -x '__pycache__' -x '*.pyc' \
  -x 'reports' -x 'data' -x 'logs' -x 'models' -x 'kb_docs' \
  -x '.pytest_cache' -x 'htmlcov' -x '.venv' -x 'dist' \
  -x 'soft_hsm_enabled_at.txt' 2>/dev/null)
echo "剩余真实差异: $REAL"

echo "=== 5. 重启主服务加载同步后代码 ==="
PID=$(ss -ltnp | grep ':18000' | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "$PID" ] && kill "$PID" && sleep 4
cd "$SRC"
set -a; source "$UAI/.env"; set +a
nohup "$UAI/venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 \
  > "$UAI/logs/api.log" 2>&1 &
for i in $(seq 1 20); do
  curl -s --max-time 3 http://127.0.0.1:18000/api/v1/health | grep -q status && break
  sleep 4
done
curl -s --max-time 3 http://127.0.0.1:18000/api/v1/health | head -c 120; echo ""
echo "=== DONE ==="
