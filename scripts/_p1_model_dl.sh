#!/usr/bin/env bash
# P1 模型层备料：Qwen3-4B / Qwen3-1.7B 官方 GGUF 并行下载（hf-mirror）
set -u
UAI="$HOME/uai"
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1

cat > "$UAI/src/scripts/_download_qwen3.sh.d" <<'EOF'
EOF
rm -f "$UAI/src/scripts/_download_qwen3.sh.d"

cat > "$UAI/src/scripts/_download_qwen3.py" <<'EOF'
import os
import sys

from huggingface_hub import hf_hub_download

target = os.path.expanduser("~/uai/models/qwen3-gguf")
os.makedirs(target, exist_ok=True)
# P1 模型层（15 号 2.1）：Qwen3 小体量候选（thinking 可关，GGUF 双引擎兼容）
REPO_FILES = {
    "Qwen/Qwen3-4B-GGUF": "Qwen3-4B-Q4_K_M.gguf",
    "Qwen/Qwen3-1.7B-GGUF": "Qwen3-1.7B-Q4_K_M.gguf",
}
repo = sys.argv[1]
fn = sys.argv[2]
p = hf_hub_download(repo_id=repo, filename=fn, local_dir=target)
print("downloaded:", p)
EOF
cd "$UAI/src"
nohup "$UAI/venv/bin/python" "$UAI/src/scripts/_download_qwen3.py" "Qwen/Qwen3-4B-GGUF" "Qwen3-4B-Q4_K_M.gguf" \
  > "$UAI/logs/qwen3_4b_dl.log" 2>&1 &
nohup "$UAI/venv/bin/python" "$UAI/src/scripts/_download_qwen3.py" "Qwen/Qwen3-1.7B-GGUF" "Qwen3-1.7B-Q4_K_M.gguf" \
  > "$UAI/logs/qwen3_17b_dl.log" 2>&1 &
echo "two downloads started"
