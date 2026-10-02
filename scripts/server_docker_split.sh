#!/usr/bin/env bash
# 第四轮：确定 Docker 与 venv 的分工。
#
# 上一轮已确认的事实：
#   * `docker.1panel.live` 可拉 Docker Hub **library/** 命名空间（hello-world ✓）；
#   * 但 `nvidia/cuda`（非 library）在该源上 `manifest unknown`。
# 由此得出的分工假设（本轮验证）：
#   * **Docker 负责基础设施**（Redis / PostgreSQL 都是 library/ 镜像）；
#   * **GPU 推理走宿主机 venv**（torch 自 PyPI 安装中，绕开 CUDA 基础镜像问题）。
set -u
ROOT=/mnt/ee16/home/student03/uai
PY="$ROOT/venv/bin/python"
M=docker.1panel.live

echo "=== A. torch 安装进度 ==="
tail -1 "$ROOT/logs/torch_install.log" 2>/dev/null
"$PY" -c 'import torch;print("torch_ready", torch.__version__, torch.cuda.is_available())' 2>&1 | tail -1
df -h /mnt/ee16 | tail -1

echo
echo "=== B. Docker 能否真的**运行**容器（userns-remap 下 pull 成功不代表 run 成功）==="
timeout 120 docker run --rm "$M/library/hello-world:latest" > /tmp/run.log 2>&1
echo "hello_world_run_rc=$?"
head -5 /tmp/run.log

echo
echo "=== C. 基础设施镜像：Redis / PostgreSQL（library/ 命名空间）==="
for img in library/redis:7-alpine library/postgres:16-alpine; do
  timeout 600 docker pull "$M/$img" > /tmp/p.log 2>&1
  rc=$?
  printf '%-40s rc=%s\n' "$M/$img" "$rc"
  tail -1 /tmp/p.log
done

echo
echo "=== D. 备选源上再试 CUDA 基础镜像（若成功，容器跑 GPU 也可行）==="
for m in docker.m.daocloud.io docker.1ms.run; do
  timeout 600 docker pull "$m/nvidia/cuda:12.4.1-base-ubuntu24.04" > /tmp/c.log 2>&1
  rc=$?
  printf '%-40s rc=%s\n' "$m/nvidia/cuda:12.4.1-base" "$rc"
  tail -1 /tmp/c.log
done

echo
echo "=== E. 镜像清单 ==="
docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' 2>/dev/null | head -8

echo
echo "=== F. 结论 ==="
echo "* B 成功且 C 成功  -> Docker 起 Redis+PG（127.0.0.1 绑定），推理走 venv"
echo "* D 任一成功       -> 还可选容器内跑 GPU 推理（需 --userns=host）"
echo "* C 失败           -> Redis/PG 也得走用户级二进制（更麻烦）"
echo "SPLIT_DONE"
