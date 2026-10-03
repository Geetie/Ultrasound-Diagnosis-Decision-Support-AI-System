#!/usr/bin/env bash
# 重启 18000（加载新路由）并验证属性提示端点
set -u
SRC=~/uai/src
LOG=~/uai/logs/api.log
cd "$SRC"
OLD=$(ss -ltnp 2>/dev/null | grep ':18000' | grep -oP 'pid=\K[0-9]+' | head -1)
if [ -n "$OLD" ]; then kill "$OLD"; sleep 4; kill -9 "$OLD" 2>/dev/null; fi
sleep 2
set -a; source ~/uai/.env; set +a
nohup ~/uai/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18000 > "$LOG" 2>&1 &
for i in $(seq 1 40); do
  c=$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:18000/api/v1/health 2>/dev/null)
  [ "$c" = "200" ] && { echo "api-ready(${i}s)"; break; }
  sleep 1
done
echo "=== 端点健康 ==="
curl -s -m 8 http://127.0.0.1:18000/api/v1/attributes/health; echo ""
curl -s -o /dev/null -w "seg_health=%{http_code}\n" -m 8 http://127.0.0.1:18000/api/v1/segmentation/health
echo "=== 真实报告文本推理（CPU 路径）==="
python - <<'PY'
import json
from app.inference.attribute_hints import AttributeHintEngine

REPORT = ("甲状腺右叶下可见一低回声结节，大小约 0.6×0.4cm，形态规则，边界清晰，"
          "内部未见明显钙化，血流信号不丰富。")
eng = AttributeHintEngine.get()
h = eng.health()
print("health:", {k: h.get(k) for k in ("available", "device")})
r = eng.hints_for_text(REPORT)
print(f"text_len={r.text_length} conflicts={r.conflicts} latency={r.latency_ms}ms device={r.device}")
for x in r.hints:
    flag = ""
    if x.report_claim:
        flag = f" | 报告声明={x.report_claim} {'一致' if x.agree else '冲突'}"
    print(f"  {x.label_zh:6} top1={x.top1:24} conf={x.confidence:.4f} margin={x.margin:.4f}{flag}")
print("禁用维度:", json.dumps(r.disabled, ensure_ascii=False))
PY
