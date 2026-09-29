#!/usr/bin/env bash
# A3：下载 Qwen2.5-7B-Instruct 双格式权重（llama.cpp GGUF Q4_K_M + vLLM fp16）
set -u
UAI="$HOME/uai"
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1
mkdir -p "$UAI/models"

"$UAI/venv/bin/python" - <<'EOF'
from huggingface_hub import snapshot_download
import os

# vLLM 用 fp16（与 1.5B 引擎的 fp16 同量化等级，公平对比）
p1 = snapshot_download(
    repo_id="Qwen/Qwen2.5-7B-Instruct",
    local_dir=os.path.expanduser("~/uai/models/Qwen2.5-7B-Instruct"),
    ignore_patterns=["*.msgpack", "*.h5", "*.ot", "flax*", "tf_*", "*.onnx", "*.gguf"],
)
print("hf done:", p1)

# llama.cpp 用 GGUF Q4_K_M（与 1.5B 引擎的 Q4_K_M 同量化等级）
p2 = snapshot_download(
    repo_id="Qwen/Qwen2.5-7B-Instruct-GGUF",
    local_dir=os.path.expanduser("~/uai/models/qwen2.5-7b-instruct-gguf"),
    allow_patterns=["*q4_k_m*"],
)
print("gguf done:", p2)
EOF
echo "=== 权重清单 ==="
ls -la "$UAI/models/Qwen2.5-7B-Instruct"/*.safetensors 2>/dev/null | awk '{print $5, $9}'
ls -la "$UAI/models/qwen2.5-7b-instruct-gguf"/*.gguf 2>/dev/null | awk '{print $5, $9}'
