#!/usr/bin/env bash
# 在服务器上起基础设施（Redis + PostgreSQL），为真机测试做前置。
#
# 设计约束（共享服务器 + userns-remap）：
#   * 只绑 127.0.0.1，且用非默认端口（16379 / 15432）—— 绝不暴露到校园网；
#   * 数据用**命名卷**（不挂宿主机目录），避开 userns-remap 导致的宿主目录属主问题；
#   * 口令在服务器本地随机生成、写入 600 权限的 .env，**脚本不回显任何口令值**；
#   * 镜像经 `docker.1panel.live` 显式拉取（裸 pull 会撞 registry-1.docker.io 拒绝）。
set -u
ROOT=/mnt/ee16/home/student03/uai
M=docker.1panel.live
ENVF="$ROOT/.env"
COMPOSE="$ROOT/docker-compose.server.yml"
cd "$ROOT" || exit 1

echo "=== A. numpy（torch 依赖）与 CUDA 复核 ==="
"$ROOT/venv/bin/python" -m pip install -q --disable-pip-version-check numpy 2>&1 | tail -2
env CUDA_VISIBLE_DEVICES=0 "$ROOT/venv/bin/python" -c 'import numpy, torch; print("numpy", numpy.__version__, "| torch", torch.__version__, "| cuda", torch.cuda.is_available())'

echo
echo "=== B. 生成 .env（口令本地随机生成，不回显）==="
if [ -f "$ENVF" ]; then
  echo ".env 已存在，保留原口令"
else
  gen() { openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | base64 | tr -d '/+='; }
  cat > "$ENVF" <<EOF
POSTGRES_USER=uai
POSTGRES_PASSWORD=$(gen)
POSTGRES_DB=uai
REDIS_PASSWORD=$(gen)
EOF
  chmod 600 "$ENVF"
  echo ".env 已生成（权限 600）"
fi
echo "包含的键：$(grep -o '^[A-Z_]*' "$ENVF" | tr '\n' ' ')"

echo
echo "=== C. 写 docker-compose.server.yml ==="
cat > "$COMPOSE" <<EOF
services:
  redis:
    image: ${M}/library/redis:7-alpine
    container_name: uai-redis
    environment:
      REDIS_PASSWORD: \${REDIS_PASSWORD}
    command: ["sh", "-c", "exec redis-server --requirepass \"\$\$REDIS_PASSWORD\" --bind 0.0.0.0 --protected-mode yes"]
    ports:
      - "127.0.0.1:16379:6379"
    volumes:
      - uai-redis-data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a \"\$\$REDIS_PASSWORD\" ping | grep -q PONG"]
      interval: 10s
      timeout: 5s
      retries: 5

  postgres:
    image: ${M}/library/postgres:16-alpine
    container_name: uai-postgres
    environment:
      POSTGRES_USER: \${POSTGRES_USER}
      POSTGRES_PASSWORD: \${POSTGRES_PASSWORD}
      POSTGRES_DB: \${POSTGRES_DB}
    ports:
      - "127.0.0.1:15432:5432"
    volumes:
      - uai-pg-data:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U \$\$POSTGRES_USER -d \$\$POSTGRES_DB"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  uai-redis-data:
  uai-pg-data:
EOF
echo "已写入 $COMPOSE"

echo
echo "=== D. 启动 ==="
docker compose --env-file "$ENVF" -f "$COMPOSE" up -d 2>&1 | tail -12

echo
echo "=== E. 等待就绪并验证连通（口令始终留在容器内）==="
sleep 15
docker compose --env-file "$ENVF" -f "$COMPOSE" ps
echo "--- redis PING ---"
docker exec uai-redis sh -lc 'redis-cli -a "$REDIS_PASSWORD" ping' 2>&1 | tail -1
echo "--- redis 是否只在本机监听（应只显示 127.0.0.1:16379）---"
ss -ltn 2>/dev/null | grep -E '16379|15432' || echo "(未见监听，稍后复查)"
echo "--- postgres ready ---"
docker exec uai-postgres pg_isready -U uai -d uai 2>&1 | tail -1
echo "--- 容器健康状态 ---"
docker inspect --format '{{.Name}} {{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' uai-redis uai-postgres 2>&1

echo
echo "=== F. 卷与空间 ==="
docker volume ls | grep -E 'uai-' || true
df -h /mnt/dd4 | tail -1
echo "INFRA_DONE"
