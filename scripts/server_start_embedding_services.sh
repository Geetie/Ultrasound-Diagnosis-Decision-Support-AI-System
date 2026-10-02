#!/usr/bin/env bash
# 启动薄嵌入/重排服务（bge-m3 :8004 / bge-reranker :8005），只绑 127.0.0.1
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
MODELS="$UAI/models"

# 用户 fish 配置里 CUDA_VISIBLE_DEVICES=2 指向不存在的卡 → 必须显式覆盖为 0
export CUDA_VISIBLE_DEVICES=0
cd "$SRC" || exit 1
mkdir -p "$UAI/logs"

pkill -f 'start_embedding_services.py --service bge-m3' && echo "bge-m3 stopped" || true
pkill -f 'start_embedding_services.py --service reranker' && echo "reranker stopped" || true
sleep 2

echo "===== start $(date '+%F %T') =====" >> "$UAI/logs/embed_bge_m3.log"
nohup "$UAI/venv/bin/python" scripts/start_embedding_services.py \
  --service bge-m3 --model-dir "$MODELS/bge-m3" --port 8004 \
  >> "$UAI/logs/embed_bge_m3.log" 2>&1 &
echo "bge-m3 pid $!"

echo "===== start $(date '+%F %T') =====" >> "$UAI/logs/embed_reranker.log"
nohup "$UAI/venv/bin/python" scripts/start_embedding_services.py \
  --service reranker --model-dir "$MODELS/bge-reranker-base" --port 8005 \
  >> "$UAI/logs/embed_reranker.log" 2>&1 &
echo "reranker pid $!"

echo "--- 等待就绪（torch CUDA 初始化 + 权重加载）---"
for i in $(seq 1 60); do
  m=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8004/health 2>/dev/null || true)
  r=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8005/health 2>/dev/null || true)
  if [ "$m" = "200" ] && [ "$r" = "200" ]; then
    echo "both ready (t≈$((i*2))s)"
    break
  fi
  sleep 2
done

echo "bge-m3   /health -> $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8004/health 2>/dev/null)"
echo "reranker /health -> $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8005/health 2>/dev/null)"
echo "--- 未就绪时的尾部日志 ---"
tail -5 "$UAI/logs/embed_bge_m3.log" 2>/dev/null | tr -d '\000'
echo "=== done ==="
