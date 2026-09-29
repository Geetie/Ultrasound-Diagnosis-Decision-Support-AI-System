#!/usr/bin/env bash
# 下载 BiomedCLIP 的 transformers 格式转换版（MIT，可直接 AutoModel 加载）
set -u
UAI="$HOME/uai"
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1
mkdir -p "$UAI/models"

"$UAI/venv/bin/python" - <<'EOF'
from huggingface_hub import snapshot_download
import os

target = os.path.expanduser("~/uai/models/biomedclip-hf")
p = snapshot_download(
    repo_id="chuhac/BiomedCLIP-vit-bert-hf",
    local_dir=target,
    ignore_patterns=["*.msgpack", "*.h5", "*.ot", "flax*", "tf_*", "*.onnx"],
)
print("downloaded to:", p)
EOF
echo "=== 文件清单 ==="
ls -la "$UAI/models/biomedclip-hf" 2>/dev/null
