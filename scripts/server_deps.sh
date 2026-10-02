#!/usr/bin/env bash
# 装项目依赖，并实测两个"非 library 命名空间"镜像是否可拉（pgvector 是 DB 侧前置）。
set -u
ROOT=/mnt/ee16/home/student03/uai
SRC=$ROOT/src
VENV=$ROOT/venv
PY=$VENV/bin/python

echo "=== A. 主依赖 requirements.txt（27 项运行时）==="
"$PY" -m pip install --disable-pip-version-check -r "$SRC/requirements.txt" > /tmp/deps1.log 2>&1
echo "rc=$?"
tail -4 /tmp/deps1.log
echo "--- 关键包版本 ---"
"$PY" -m pip list 2>/dev/null | grep -iE '^(fastapi|uvicorn|sqlalchemy|asyncpg|pgvector|redis|alembic|structlog|pydantic|numpy|torch|httpx|minio) ' | head -14

echo
echo "=== B. 向量/模型依赖 requirements-emb.txt（torch 已装，余下为 transformers/peft 等）==="
"$PY" -m pip install --disable-pip-version-check -r "$SRC/requirements-emb.txt" > /tmp/deps2.log 2>&1
echo "rc=$?"
tail -3 /tmp/deps2.log

echo
echo "=== C. 非 library 命名空间镜像实测（pgvector / nvidia）==="
for m in docker.1panel.live docker.m.daocloud.io docker.1ms.run; do
  timeout 300 docker pull "$m/pgvector/pgvector:pg16" > /tmp/pv.log 2>&1
  echo "$m/pgvector/pgvector:pg16 rc=$?"
  tail -1 /tmp/pv.log
done

echo
echo "=== D. 当前镜像清单 ==="
docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | head -10

echo
echo "=== E. 源码树抽查（确认是完整仓库）==="
ls "$SRC" | wc -l
ls "$SRC/app" | head -12
ls "$SRC/alembic/versions" 2>/dev/null | head -8

echo
echo "=== F. 磁盘 ==="
df -h /mnt/ee16 | tail -1
echo "DEPS_DONE"
