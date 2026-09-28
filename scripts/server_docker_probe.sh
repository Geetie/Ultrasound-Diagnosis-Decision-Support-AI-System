#!/usr/bin/env bash
# 第三轮：修正镜像名（去掉 https:// 前缀）后实测 Docker 拉取与 GPU 直通。
#
# 上一轮失败原因（记下来免得重踩）：把探测用的 URL 直接当镜像名前缀，
# 形成 `https://docker.1panel.live/library/hello-world` -> invalid reference format。
# 正确写法是**只取主机名**：`docker.1panel.live/library/hello-world:latest`。
# 另：`docker pull ... | tail` 会让 $? 变成 tail 的退出码，所以这里一律先落日志再取 rc。
set -u
ROOT=/mnt/ee16/home/student03/uai
PY="$ROOT/venv/bin/python"

echo "=== A. torch 进度（后续需要它做数值测试）==="
tail -1 "$ROOT/logs/torch_install.log" 2>/dev/null
"$PY" -c 'import torch;print("torch_ready", torch.__version__, torch.cuda.is_available())' 2>&1 | tail -1
df -h /mnt/ee16 | tail -1

echo
echo "=== B. 用可用加速源拉 hello-world（极小，验证通道）==="
OK_MIRROR=""
for M in docker.1panel.live docker.m.daocloud.io docker.1ms.run; do
  echo "--- 试 $M ---"
  timeout 180 docker pull "$M/library/hello-world:latest" > /tmp/pull_hw.log 2>&1
  rc=$?
  tail -2 /tmp/pull_hw.log
  echo "rc=$rc"
  if [ "$rc" -eq 0 ]; then OK_MIRROR="$M"; break; fi
done
echo "可用加速源: ${OK_MIRROR:-（无）}"

if [ -z "$OK_MIRROR" ]; then
  echo "hello-world 都拉不下来 -> Docker 通道仍不通，继续 venv 路线"
  echo "DOCKER_PROBE_DONE"
  exit 0
fi

echo
echo "=== C. 拉 nvidia/cuda base（~250MB，用于 GPU 直通实测）==="
CUDA_IMG="$OK_MIRROR/nvidia/cuda:12.4.1-base-ubuntu24.04"
timeout 900 docker pull "$CUDA_IMG" > /tmp/pull_cuda.log 2>&1
rc=$?
tail -3 /tmp/pull_cuda.log
echo "cuda_pull_rc=$rc"

echo
echo "=== D. GPU 直通三路实测 ==="
if [ "$rc" -ne 0 ]; then
  echo "cuda 镜像未拉到，跳过 GPU 实测"
else
  echo "--- D1: --gpus all（daemon 原样，含 userns-remap）---"
  timeout 300 docker run --rm --gpus all "$CUDA_IMG" nvidia-smi --query-gpu=name,memory.total --format=csv > /tmp/d1.log 2>&1
  echo "D1_rc=$?"; tail -3 /tmp/d1.log

  echo "--- D2: --userns=host --gpus all ---"
  timeout 300 docker run --rm --userns=host --gpus all "$CUDA_IMG" nvidia-smi --query-gpu=name,memory.total --format=csv > /tmp/d2.log 2>&1
  echo "D2_rc=$?"; tail -3 /tmp/d2.log

  echo "--- D3: --runtime=nvidia --userns=host ---"
  timeout 300 docker run --rm --runtime=nvidia --userns=host "$CUDA_IMG" nvidia-smi --query-gpu=name,memory.total --format=csv > /tmp/d3.log 2>&1
  echo "D3_rc=$?"; tail -3 /tmp/d3.log
fi

echo
echo "=== E. 镜像清单与磁盘 ==="
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' 2>/dev/null | head -6
df -h /mnt/dd4 | tail -1

echo
echo "=== F. 结论 ==="
echo "D1 成功 -> 可直接用 Docker（compose 无需特殊设置）"
echo "D2/D3 成功 -> compose 里所有服务加 userns_mode: host（并视情况加 runtime: nvidia）"
echo "全失败 -> 容器内拿不到 GPU，改用宿主机 venv 跑推理"
echo "DOCKER_PROBE_DONE"
