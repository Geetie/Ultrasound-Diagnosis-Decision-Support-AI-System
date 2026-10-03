#!/usr/bin/env bash
# 交付关键验证：完整链路 + 报告正文（重点看诊断意见是否有 LLM 内容）
set -u
cd ~/uai/src
set -a; source ~/uai/.env; set +a
export PYTHONPATH=~/uai/src
export NODULE_CONTOUR_IN_PIPELINE_ENABLED=1

pkill -9 -f "uvicorn app.main:app --host 0.0.0.0 --port 18000" 2>/dev/null || true
sleep 3
export CUDA_VISIBLE_DEVICES=0
setsid nohup ~/uai/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18000 \
  --log-level warning > /tmp/uvicorn18000.log 2>&1 < /dev/null &
disown
for i in $(seq 1 40); do
  sleep 3
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:18000/api/v1/health 2>/dev/null)
  [ "$c" = "200" ] && { echo "restarted ($((i*3))s)"; break; }
done

IMG=$(ls ~/uai/data/tn3k_dataset/test-image/* 2>/dev/null | head -1)
echo "img=$IMG"
IMG_PATH="$IMG" ~/uai/venv/bin/python ~/uai/src/scripts/deploy_drill_e2e.py 2>&1 | tail -20

echo "=== 报告正文（诊断意见段）==="
~/uai/venv/bin/python /tmp/_drd.py 2>&1 | sed -n '/四、诊断意见/,/五、建议/p'
echo "=== 质量控制段 ==="
~/uai/venv/bin/python /tmp/_drd.py 2>&1 | sed -n '/八、质量控制/,$p' | head -12
echo "=== LLM 相关日志 ==="
grep -iE "llm|vllm|llama|generate" /tmp/uvicorn18000.log | tail -6 | cut -c1-180
