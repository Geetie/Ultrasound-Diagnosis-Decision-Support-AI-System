#!/usr/bin/env bash
set -u
"$HOME/uai/venv/bin/python" - <<'EOF'
import json

d = json.load(open("/mnt/ee16/home/student03/uai/reports/consistency_780_guided_v2_report.json"))
print("guided v2（llama-server 载体）:")
print("  总体一致率:", d.get("grading_consistency_rate"), "| 边界差:", d.get("clinical_boundary_diff_rate"))
st = d.get("stratified_criteria", {})
for k, v in st.items():
    if isinstance(v, dict) and v:
        print(f"  {k}: {json.dumps(v, ensure_ascii=False)}")
for organ, v in (d.get("organ_breakdown") or {}).items():
    print(f"  {organ}: n={v.get('total')} 一致率={v.get('grading_consistency_rate')}")
print("延迟: a_p50=", d.get("latency_a_p50_ms"), "b_p50=", d.get("latency_b_p50_ms"))
EOF
