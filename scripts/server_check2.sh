#!/usr/bin/env bash
# 第二轮：① 确认 torch/CUDA ② 打通 Docker 镜像源 ③ 实测 GPU 直通。
# 只读 + 拉取镜像；不改系统配置、不动其他用户资源。
set -u
ROOT=/mnt/ee16/home/student03/uai
PY="$ROOT/venv/bin/python"

echo "=== A. torch 安装进度与 CUDA 可用性 ==="
tail -2 "$ROOT/logs/torch_install.log" 2>/dev/null || echo "(无日志)"
if "$PY" -c 'import torch' 2>/dev/null; then
  "$PY" <<'PY'
import torch
print("torch:", torch.__version__)
print("cuda_available:", torch.cuda.is_available())
print("device_count:", torch.cuda.device_count())
if torch.cuda.is_available():
    print("gpu:", torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))
PY
else
  echo "torch 仍未装完；后台进程:"
  pgrep -af 'pip install' | head -3
fi
df -h /mnt/ee16 | tail -1

echo
echo "=== B. daemon 实际生效的镜像加速与安全选项 ==="
docker info --format 'Mirrors={{json .RegistryConfig.Mirrors}}'
docker info --format 'Security={{json .SecurityOptions}}'

echo
echo "=== C. 逐个探测候选镜像源 /v2/（401/200=可达，000=不可达）==="
MIRRORS="
https://docker.1panel.live
https://docker.m.daocloud.io
https://docker.1ms.run
https://docker.imgdb.de
https://docker.nju.edu.cn
https://docker.mirrors.ustc.edu.cn
https://docker.mirrors.sjtug.sjtu.edu.cn
https://hub-mirror.c.163.com
https://quay.io
https://ghcr.io
https://registry-1.docker.io
"
WORKING=""
for m in $MIRRORS; do
  code=$(curl -sS -o /dev/null -m 8 -w '%{http_code}' "$m/v2/" 2>/dev/null)
  printf '%-46s http=%s\n' "$m" "${code:-none}"
  if [ -z "$WORKING" ] && { [ "$code" = "401" ] || [ "$code" = "200" ]; }; then
    WORKING="$m"
  fi
done
echo "首个可用源: ${WORKING:-（无）}"

echo
echo "=== D. 用可用源实测拉取（hello-world，极小）==="
if [ -z "$WORKING" ]; then
  echo "无可用源 → Docker 路线放弃，继续 venv"
else
  timeout 300 docker pull "${WORKING}/library/hello-world:latest" 2>&1 | tail -4
  echo "pull_rc=$?"
  docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' 2>/dev/null | head -5
fi

echo
echo "=== E. GPU 直通实测（nvidia/cuda base，若 D 成功）==="
if docker image ls --format '{{.Repository}}' 2>/dev/null | grep -q . ; then
  CUDA_IMG="${WORKING}/nvidia/cuda:12.4.1-base-ubuntu24.04"
  timeout 900 docker pull "$CUDA_IMG" 2>&1 | tail -3
  echo "cuda_pull_rc=$?"
  echo "--- 尝试 A: 默认（daemon 启用 userns-remap）---"
  timeout 300 docker run --rm --gpus all "$CUDA_IMG" nvidia-smi --query-gpu=name,memory.total --format=csv 2>&1 | tail -4
  echo "A_rc=$?"
  echo "--- 尝试 B: --userns=host ---"
  timeout 300 docker run --rm --userns=host --gpus all "$CUDA_IMG" nvidia-smi --query-gpu=name,memory.total --format=csv 2>&1 | tail -4
  echo "B_rc=$?"
else
  echo "D 未成功，跳过 GPU 容器实测"
fi

echo
echo "=== F. 结论 ==="
echo "A 成功 -> 容器内可见 GPU，可走 Docker 部署"
echo "仅 B 成功 -> 部署时所有服务需加 --userns=host / compose 的 userns_mode: host"
echo "都失败 -> Docker 路线放弃，走宿主机 venv"
echo "CHECK2_DONE"
