#!/usr/bin/env bash
# 属性提示真机验证：CPU 路径（GPU 被 vLLM 占满）+ 与报告声明的一致性
set -u
SRC=~/uai/src
cd "$SRC"
set -a; source ~/uai/.env; set +a

echo "=== health ==="
curl -s -m 8 http://127.0.0.1:18000/api/v1/attributes/health; echo ""

echo "=== 真实报告文本（甲状腺 TI-RADS 4a 描述）==="
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
for h_ in r.hints:
    flag = ""
    if h_.report_claim:
        flag = f" | 报告声明={h_.report_claim} {'✓一致' if h_.agree else '✗冲突'}"
    print(f"  {h_.label_zh:6} top1={h_.top1:24} conf={h_.confidence:.4f} margin={h_.margin:.4f}{flag}")
print("禁用维度:", json.dumps(r.disabled, ensure_ascii=False))
PY
