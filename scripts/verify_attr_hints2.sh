#!/usr/bin/env bash
# 属性提示真机验证（长超时：首次懒加载 CLIP + ULC 权重需 10~30s）
set -u
cd ~/uai/src
set -a; source ~/uai/.env; set +a
PY=~/uai/venv/bin/python

echo "=== 1) 端点 health（180s 超时）==="
time curl -s -m 180 http://127.0.0.1:18000/api/v1/attributes/health; echo ""

echo "=== 2) 真实报告文本推理（进程内直调，测 CPU 路径）==="
CUDA_VISIBLE_DEVICES= PYTHONPATH=/mnt/ee16/home/student03/uai/src "$PY" - <<'PY'
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

REPORT2 = "甲状腺内低回声结节，边界欠清，形态不规则，血流丰富。"
r2 = eng.hints_for_text(REPORT2)
print(f"\n第二份报告（故意写冲突）conflicts={r2.conflicts}:")
for x in r2.hints:
    if x.report_claim:
        print(f"  {x.label_zh:6} 报告声明={x.report_claim} 模型={x.top1} -> {'一致' if x.agree else '冲突'}")
PY

echo "=== 3) seg 端点复验 ==="
curl -s -m 30 -o /dev/null -w "seg_health=%{http_code}\n" http://127.0.0.1:18000/api/v1/segmentation/health
