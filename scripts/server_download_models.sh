#!/usr/bin/env bash
# 经 hf-mirror 下载推理权重到 ~/uai/models（可续传、幂等、只绑本地）
#
# 说明（2026-09-28）：
# - 本机 huggingface.co 不可达，必须走 HF_ENDPOINT=https://hf-mirror.com；
# - `huggingface_hub` 的下载是**分块+续传**的，中断后重跑同一命令会接着下；
# - 模型清单来自项目自身的默认值，不是自选：
#     LLM(compact/llama.cpp)  scripts/llama_cpp_server.py  → Qwen2.5-1.5B-Instruct-GGUF
#     文本嵌入(1024d)          app/embedding/bge_m3.py       → bge-m3-medical（公开基座 BAAI/bge-m3）
#     重排                     settings.bge_reranker_model   → bge-reranker-base
#   **Ultrasound-CLIP（768d 图像嵌入）是项目自有产物**（由
#   scripts/finetune_ultrasound_clip.py 训练得到，公开源没有），本脚本不下载它。
#   用法：`bash server_download_models.sh [--with-vllm]`
set -u
UAI="$HOME/uai"
MODELS="$UAI/models"
LOG_DIR="$UAI/logs"
mkdir -p "$MODELS" "$LOG_DIR"

export HF_ENDPOINT=https://hf-mirror.com
export HF_HOME="$MODELS/hf"
export HF_HUB_DISABLE_TELEMETRY=1
export HF_HUB_ENABLE_HF_TRANSFER=0
# ⚠️ 必须关掉 Xet 传输（2026-09-28 实测）：huggingface_hub ≥0.32 默认用 Xet 存储后端，
# 它会去 `cas-server.xethub.hf.co` 取分块 —— 那个域名**没有镜像**，于是报
#   RuntimeError: Task error: File reconstruction error: CAS Client Error:
#   HTTP status client error (401 Unauthorized), domain: https://cas-server.xethub.hf.co/...
# 关掉后回退到常规 HTTP（走 HF_ENDPOINT 镜像），下载正常。
export HF_HUB_DISABLE_XET=1

WITH_VLLM=0
[ "${1:-}" = "--with-vllm" ] && WITH_VLLM=1

if [ -x "$UAI/venv/bin/hf" ]; then
  DL="$UAI/venv/bin/hf"
elif [ -x "$UAI/venv/bin/huggingface-cli" ]; then
  DL="$UAI/venv/bin/huggingface-cli"
else
  echo "ERROR: 找不到 hf / huggingface-cli（应在 $UAI/venv/bin）"
  exit 1
fi

log() { echo "[$(date '+%F %T')] $*"; }

log "dl=$DL  HF_ENDPOINT=$HF_ENDPOINT  HF_HOME=$HF_HOME"

# 1) LLM：compact/llama.cpp 轨道用的 GGUF（Q4_K_M，约 1.1GB）
log "==> Qwen2.5-1.5B-Instruct-GGUF (q4_k_m)"
"$DL" download Qwen/Qwen2.5-1.5B-Instruct-GGUF \
  qwen2.5-1.5b-instruct-q4_k_m.gguf \
  --local-dir "$MODELS/qwen2.5-1.5b-instruct-gguf" || log "WARN: GGUF 下载失败（继续）"
# scripts/start_local_test_services.py 在 models/ 根目录按文件名找 GGUF
if [ -f "$MODELS/qwen2.5-1.5b-instruct-gguf/qwen2.5-1.5b-instruct-q4_k_m.gguf" ]; then
  ln -sf "$MODELS/qwen2.5-1.5b-instruct-gguf/qwen2.5-1.5b-instruct-q4_k_m.gguf" \
         "$MODELS/qwen2.5-1.5b-instruct-q4_k_m.gguf"
fi

# 2) 文本嵌入：bge-m3（1024d，供 bge_m3 服务与 0012 的 embedding_bge 列）
#
# ⚠️ 必须排除两类路径（2026-09-28 实测）：
#   * `imgs/*`、`*.DS_Store` —— 仓库里带着 macOS 垃圾文件，hf-mirror 对它们返回
#     `403 Forbidden: Cannot access content at .../imgs%2F.DS_Store`，
#     而**一个文件失败会中断整次下载**（第一次就栽在这里，只落 720K）；
#   * `onnx/*` —— 导出产物（约 2GB），本项目的嵌入服务走 transformers，用不到。
log "==> BAAI/bge-m3"
# ⚠️ 每个 pattern 都要单独 `--exclude`：`hf download` 的多值选项只吃**第一个**值，
# 其余会被当成"显式指定的文件名"（实测警告：
#   UserWarning: Ignoring `--exclude` since filenames have been explicitly set.
# 于是 imgs/.DS_Store 照样被请求 → 403 → 整次下载中断，config.json 都没落地。
"$DL" download BAAI/bge-m3 --local-dir "$MODELS/bge-m3" \
  --exclude "imgs/*" \
  --exclude "onnx/*" \
  --exclude "*.DS_Store" \
  --exclude ".gitattributes" \
  || log "WARN: bge-m3 下载失败（继续）"

# 3) 重排：bge-reranker-base
log "==> BAAI/bge-reranker-base"
"$DL" download BAAI/bge-reranker-base --local-dir "$MODELS/bge-reranker-base" || log "WARN: reranker 下载失败（继续）"

# 4) 可选：full 档（vLLM）要的 HF 格式权重（约 3GB）
if [ "$WITH_VLLM" = "1" ]; then
  log "==> Qwen/Qwen2.5-1.5B-Instruct（vLLM 档，约 3GB）"
  "$DL" download Qwen/Qwen2.5-1.5B-Instruct --local-dir "$MODELS/Qwen2.5-1.5B-Instruct" \
    || log "WARN: HF 权重下载失败（继续）"
fi

log "==> 占用情况"
du -sh "$MODELS"/* 2>/dev/null || true
log "==> done"
