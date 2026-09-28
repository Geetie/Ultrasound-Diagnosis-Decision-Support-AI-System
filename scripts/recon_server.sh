#!/usr/bin/env bash
# 目标服务器只读侦察脚本（真机部署前的第一步）。
#
# 设计原则：
#   * **只读**：不安装任何包、不改配置、不需要 sudo、不落任何文件（除 stdout）；
#   * **无交互**：全部命令带超时/静默参数，可在 ssh 一行内跑完；
#   * **输出可粘贴**：按节打印，便于整段贴回。
#
# 用法（在目标服务器上，普通用户即可）：
#     bash recon_server.sh 2>&1 | head -220

set -u
hr() { printf '\n=========== %s ===========\n' "$1"; }

hr "1. 主机与系统"
uname -a 2>/dev/null
[ -r /etc/os-release ] && . /etc/os-release && echo "OS: ${PRETTY_NAME:-?}"
echo "当前用户: $(id -un) (uid=$(id -u))  组: $(id -Gn 2>/dev/null)"
echo "家目录: $HOME"
uptime 2>/dev/null

hr "2. CPU / 内存"
grep -m1 'model name' /proc/cpuinfo 2>/dev/null
echo "逻辑核数: $(nproc 2>/dev/null)"
free -h 2>/dev/null

hr "3. GPU（决定能否跑 vLLM / TensorRT）"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.used,utilization.gpu \
             --format=csv 2>/dev/null
  nvidia-smi | grep -i 'CUDA Version' 2>/dev/null
else
  echo "无 nvidia-smi（未见 NVIDIA 驱动）"
fi
command -v nvcc >/dev/null 2>&1 && nvcc --version | tail -2 || echo "无 nvcc（无 CUDA Toolkit）"
ls /usr/local/ 2>/dev/null | grep -i cuda || echo "无 /usr/local/cuda*"

hr "4. 磁盘可用空间（模型权重按 GB 计）"
df -hT 2>/dev/null | grep -Ev 'tmpfs|devtmpfs|overlay' | head -20
echo "--- 家目录配额 ---"
quota -s 2>/dev/null || echo "（无 quota 信息）"

hr "5. Python / 工具链"
for py in python3 python3.13 python3.12 python3.11 python3.10 python; do
  command -v "$py" >/dev/null 2>&1 && printf '%s -> %s\n' "$py" "$($py -V 2>&1)"
done
echo "pip: $(python3 -m pip -V 2>&1 | head -1)"
echo "venv: $(python3 -c 'import venv;print("ok")' 2>&1)"
echo "git: $(git --version 2>&1)"
echo "docker: $(docker --version 2>&1)"
echo "docker compose: $(docker compose version 2>&1 | head -1)"
echo "make: $(make --version 2>&1 | head -1)"
echo "gcc: $(gcc --version 2>&1 | head -1)"

hr "6. 已有相关进程（避免与别人冲突）"
ps -eo pid,user,etime,cmd 2>/dev/null | grep -Ei 'python|uvicorn|vllm|llama|redis|postgres|docker' | grep -v grep | head -15

hr "7. 端口占用（本项目默认 8000/8001/8002/9001/9090/5432/6379）"
if command -v ss >/dev/null 2>&1; then ss -ltn 2>/dev/null | head -30
elif command -v netstat >/dev/null 2>&1; then netstat -ltn 2>/dev/null | head -30
else echo "无 ss/netstat"; fi

hr "8. 外网与模型仓库可达性（本机实测 huggingface.co 不通、hf-mirror.com 通）"
for url in https://hf-mirror.com https://huggingface.co https://zenodo.org https://pypi.org; do
  code=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$url" 2>/dev/null || echo fail)
  printf '%-32s %s\n' "$url" "$code"
done
echo "--- pip 源配置 ---"
python3 -m pip config list 2>/dev/null || echo "（未配置 pip 源）"

hr "9. systemd 用户级能力（常驻服务用）"
loginctl show-user "$(id -un)" 2>/dev/null | grep -E 'Linger|State' || echo "无法查询 loginctl"

hr "10. 资源实况快照（部署后做基线对比）"
( command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader ) 2>/dev/null
echo "负载: $(cat /proc/loadavg 2>/dev/null)"

hr "侦察结束"
echo "脚本未改动系统任何状态。"
