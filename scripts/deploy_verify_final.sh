#!/usr/bin/env bash
# 最终复验：离线闸门 + 多器官分割 + 中文护栏 + 服务健康
set -u
cd ~/uai/src
set -a; source ~/uai/.env; set +a
LOG=/tmp/_final_verify.log
: > "$LOG"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

say "=== 0. 重启 18000（带 ~/uai/.env）==="
pkill -9 -f "uvicorn app.main:app --host 0.0.0.0 --port 18000" 2>/dev/null || true
sleep 3
cd ~/uai/src
export CUDA_VISIBLE_DEVICES=0 PYTHONPATH=~/uai/src
setsid nohup ~/uai/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18000 --log-level warning > /tmp/uvicorn18000.log 2>&1 < /dev/null &
disown
for i in $(seq 1 40); do
  sleep 3
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:18000/api/v1/health 2>/dev/null)
  [ "$c" = "200" ] && { say "health OK ($((i*3))s)"; break; }
done

MARK="__FINAL_SENTINEL__"
echo "$MARK" >> /tmp/uvicorn18000.log

say "=== 1. 英文属性提示（真实装载 CLIP；不得联网）==="
t0=$(date +%s)
code=$(curl -s -o /tmp/_h.json -w "%{http_code}" --max-time 120 -X POST \
  http://127.0.0.1:18000/api/v1/attributes/hints -H "Content-Type: application/json" \
  -d '{"image_path":"/tmp/uai_nodata.jpg","text":"hypoechoic nodule with ill-defined margins"}')
say "  http=$code elapsed=$(( $(date +%s)-t0 ))s"
head -c 160 /tmp/_h.json; echo "" | tee -a "$LOG"

say "=== 2. 中文护栏（应 E4005 拒中文）==="
curl -s --max-time 30 -X POST http://127.0.0.1:18000/api/v1/attributes/hints \
  -H "Content-Type: application/json" \
  -d '{"image_path":"/tmp/uai_nodata.jpg","text":"甲状腺低回声结节"}' \
  | head -c 180; echo "" | tee -a "$LOG"

say "=== 3. HF 联网痕迹（必须 0）==="
LEAK=$(awk -v m="$MARK" 'index($0,m){f=1;next} f' /tmp/uvicorn18000.log \
       | grep -ciE "huggingface\.co|Retrying in|Network is unreachable" || true)
say "  HF_NETWORK_LEAKS=$LEAK"

say "=== 4. 多器官分割矩阵 ==="
curl -s --max-time 120 http://127.0.0.1:18000/api/v1/segmentation/organs \
 | ~/uai/venv/bin/python -c "import json,sys;[print(f\"  {k:9} {v['status']:16} dice={v['metrics'].get('dice')}\") for k,v in sorted(json.load(sys.stdin)['organs'].items())]" 2>&1 | tee -a "$LOG"

say "=== 5. 六端点 ==="
for ep in /api/v1/health /api/v1/diagnosis/request /api/v1/attributes/health /api/v1/imaging/dicom/upload /api/v1/report/templates /api/v1/segmentation/organs; do
  printf "  %-38s %s\n" "$ep" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "http://127.0.0.1:18000${ep}")"
done

say "=== 6. 其他人的服务未受影响（只读观察）==="
for p in 8002 8003 8006 9001; do
  printf "  %s -> %s\n" "$p" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:$p/health 2>/dev/null || echo down)"
done

say "DONE"
