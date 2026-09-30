#!/usr/bin/env bash
# Langfuse 栈部署：密钥生成 + 起服务 + 健康检查
set -u
UAI="$HOME/uai"
DIR="$UAI/langfuse"
mkdir -p "$DIR"
cd "$DIR"

if [ ! -f .env ]; then
  echo "=== 生成密钥 ==="
  cat > .env <<EOF
NEXTAUTH_SECRET=$(openssl rand -base64 32)
SALT=$(openssl rand -base64 32)
ENCRYPTION_KEY=$(openssl rand -hex 32)
LANGFUSE_PG_PASSWORD=$(openssl rand -hex 16)
CLICKHOUSE_PASSWORD=$(openssl rand -hex 16)
MINIO_ROOT_PASSWORD=$(openssl rand -hex 16)
REDIS_PASSWORD=$(openssl rand -hex 16)
EOF
  chmod 600 .env
  echo ".env 生成（密钥已落盘 ~/uai/langfuse/.env）"
fi

cp "$UAI/src/scripts/langfuse/docker-compose.yml" docker-compose.yml
echo "=== 起栈 ==="
docker compose up -d 2>&1 | tail -8
echo "=== 等待 web 健康（最长 3 分钟）==="
for i in $(seq 1 90); do
  sleep 2
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:30100/api/public/health 2>/dev/null || true)
  [ "$code" = "200" ] && { echo "langfuse healthy (t≈$((i*2))s)"; exit 0; }
done
echo "langfuse 未就绪，日志："
docker compose logs --tail 10 langfuse-web 2>&1 | tail -10
exit 1
