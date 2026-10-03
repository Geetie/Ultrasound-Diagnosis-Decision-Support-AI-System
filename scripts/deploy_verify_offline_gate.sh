#!/usr/bin/env bash
# 离线闸门专项验证：属性提示请求**不得**触发任何 huggingface.co 访问，
# 且不得阻塞 event loop（请求应在合理时间内返回，服务保持响应）。
set -u
cd ~/uai/src
set -a; source ~/uai/.env; set +a
LOG=/tmp/_offline_verify.log
: > "$LOG"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

MARK="__HF_LEAK_CHECK__"
grep -c "$MARK" /tmp/uvicorn18000.log > /dev/null 2>&1 && say "warn: 日志已有标记"

say "=== 0. 重启（带新代码）==="
pkill -9 -f "uvicorn app.main:app --host 0.0.0.0 --port 18000" 2>/dev/null || true
sleep 3
bash /tmp/_start18000.sh > /tmp/_restart2.log 2>&1
tail -1 /tmp/_restart2.log
curl -s -o /dev/null -w "health -> %{http_code}\n" --max-time 10 http://127.0.0.1:18000/api/v1/health

say "=== 1. 基线：日志行数 + 当前时间 ==="
BEFORE=$(wc -l < /tmp/uvicorn18000.log)
echo "log lines before: $BEFORE" | tee -a "$LOG"
# 在日志里放一个哨兵，之后只看它之后的日志
echo "$MARK $(date +%s)" >> /tmp/uvicorn18000.log

say "=== 2. 触发属性提示（英文，真实路径会装载 CLIP）==="
t0=$(date +%s)
code=$(curl -s -o /tmp/_hints_resp.json -w "%{http_code}" --max-time 120 \
  -X POST http://127.0.0.1:18000/api/v1/attributes/hints \
  -H "Content-Type: application/json" \
  -d '{"image_path":"/tmp/uai_nodata.jpg","text":"hypoechoic nodule with ill-defined margins"}')
t1=$(date +%s)
say "  http=$code  elapsed=$((t1-t0))s"
head -c 300 /tmp/_hints_resp.json; echo "" | tee -a "$LOG"

say "=== 3. 服务是否仍响应（关键：event loop 没被卡死）==="
curl -s -o /dev/null -w "  health after -> %{http_code}\n" --max-time 10 http://127.0.0.1:18000/api/v1/health

say "=== 4. 哨兵之后是否出现 HF 联网/重试痕迹（必须为 0）==="
AFTER=$(awk -v m="$MARK" 'index($0,m){found=NR} found && NR>found' /tmp/uvicorn18000.log | wc -l)
LEAK=$(awk -v m="$MARK" 'index($0,m){f=1;next} f' /tmp/uvicorn18000.log \
       | grep -ciE "huggingface\.co|Retrying in|Network is unreachable|open_clip_model" || true)
say "  lines_after_sentinel=$AFTER"
say "  HF_NETWORK_LEAKS=$LEAK   （必须为 0）"

say "=== 5. 分割多器官回归（确认没被离线闸门误伤）==="
curl -s --max-time 90 "http://127.0.0.1:18000/api/v1/segmentation/health?organ=cardiac" \
  | head -c 200; echo "" | tee -a "$LOG"

say "=== 6. 旁路开关仍是默认关闭 ==="
curl -s --max-time 15 http://127.0.0.1:18000/api/v1/attributes/health | head -c 220; echo "" | tee -a "$LOG"

say "DONE"
