"""4.4 复核：用实测延迟算「生产 30s 超时」命中率（可复算，不靠叙述）。

用 p107 的实测延迟分布，直接算「生产 30s 超时」会命中多少比例（可复算，不靠叙述）。

用法：
    ~/uai/venv/bin/python scripts/audit/p107b_latency_timeout_profile.py
"""

import json
import pathlib

_REPORT = pathlib.Path.home() / "uai/reports/p107_grading_consistency.json"
with _REPORT.open(encoding="utf-8") as fh:
    d = json.load(fh)
lat = [x for r in d["rows"] for x in (r.get("latencies_s") or [])]
n = len(lat)
over30 = sum(1 for x in lat if x > 30)
over60 = sum(1 for x in lat if x > 60)
print(f"生成调用 {n} 次（单次 = 1 个输入的 1 次采样）")
print(f"  > 30s（生产 INFERENCE_TIMEOUT_SECONDS=30）: {over30} = {over30/n:.2%}")
print(f"  > 60s: {over60} = {over60/n:.2%}")
s = sorted(lat)
print(f"  p50={s[n//2]:.1f}s p90={s[int(n*0.9)]:.1f}s max={s[-1]:.1f}s")
print(f"  min={s[0]:.1f}s")
# 按输入算：至少一次超过 30s 的输入
rows = d["rows"]
inputs_any = sum(1 for r in rows if any(x > 30 for x in (r.get("latencies_s") or [])))
print(f"至少一次采样 >30s 的输入: {inputs_any}/{len(rows)}")
