#!/usr/bin/env bash
# 只读侦察：列出真实 API 端点与关键 schema 字段。
# 用途：前端接线必须以真机 OpenAPI 为准，不能凭设计文档猜端点名。
set -uo pipefail

BASE=http://127.0.0.1:18000

echo "=== 1. 端点清单 ==="
curl -s --max-time 20 "$BASE/openapi.json" \
  | python3 -c '
import sys, json
d = json.load(sys.stdin)
for p, v in sorted(d["paths"].items()):
    for m in v:
        if m in ("get", "post", "put", "patch", "delete"):
            print(m.upper().ljust(6), p)
'

echo
echo "=== 2. 诊断结果 schema 里那些「设计文档说恒为常量」的字段 ==="
curl -s --max-time 20 "$BASE/openapi.json" \
  | python3 -c '
import sys, json
d = json.load(sys.stdin)
schemas = d.get("components", {}).get("schemas", {})
targets = ["consistency_flag", "fusion_status", "self_rag_passed",
           "ai_diagnosis_failed", "hallucination_severity", "review",
           "requires_manual_review", "desensitization_status"]
found = False
for name, s in sorted(schemas.items()):
    props = (s or {}).get("properties") or {}
    hit = [t for t in targets if t in props]
    if hit:
        found = True
        print("[%s]" % name)
        for t in hit:
            v = props[t]
            enum = v.get("enum")
            default = v.get("default")
            typ = v.get("type")
            print("   %-26s type=%-8s default=%-12s enum=%s"
                  % (t, typ, default, enum if enum else "-"))
if not found:
    print("!! 目标字段一个都没出现在 OpenAPI schema 里")
    print("   可用 schema 名单:")
    for name in sorted(schemas):
        print("   -", name)
'
