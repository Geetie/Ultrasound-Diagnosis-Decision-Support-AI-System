#!/usr/bin/env bash
# 真机链路冒烟：健康检查 + 一次最小诊断请求（**不产出任何数值结论**）
#
# 说明：
# - 前置数据（患者/会话/同意）直接 INSERT 落库：本脚本要验证的是**诊断链路的控制面**
#   （鉴权 → 限流 → 幂等 → 入队 → worker → 管线 → SG 校验 → 落库 → 监控），
#   不是会话/同意 API 的入参契约，没必要去猜它们的 payload。
# - 文本为**自造内容**，不含任何真实患者数据（共享机红线）。
# - 只打印 HTTP 码、状态枚举与日志事件名；**不打印任何精度/时延/一致性数值**。
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
cd "$SRC" || exit 1
set -a
# shellcheck disable=SC1091
. "$UAI/.env"
set +a

API=http://127.0.0.1:18000
SG=http://127.0.0.1:9001
PSQL="docker exec uai-postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -tA -q"

PATIENT_ID="smoke-patient-0001"
SESSION_ID="smoke-session-0001"

echo "=== 1) 健康检查 ==="
echo "api  /api/v1/health -> $(curl -s -o /tmp/sm_h.json -w '%{http_code}' --max-time 5 $API/api/v1/health)"
"$UAI/venv/bin/python" - <<'PY'
import json, pathlib
try:
    d = json.loads(pathlib.Path("/tmp/sm_h.json").read_text())
    print("   status:", d.get("status"), "| services:", {k: (v.get("status") if isinstance(v, dict) else v) for k, v in (d.get("services") or {}).items()})
except Exception as e:
    print("   解析失败:", e)
PY
echo "sg   /health -> $(curl -s -o /tmp/sm_sg.json -w '%{http_code}' --max-time 5 $SG/health)"

echo
echo "=== 2) 构造前置数据（幂等）==="
$PSQL -c "INSERT INTO patients (patient_id, name, gender) VALUES ('$PATIENT_ID', 'SMOKE-PATIENT', 'unknown') ON CONFLICT (patient_id) DO NOTHING" 2>&1 | tail -1
$PSQL -c "INSERT INTO sessions (session_id, patient_id, device_id, status, target_organs, organ_statuses, knowledge_base_version, created_at, updated_at, consent_status_at_creation) VALUES ('$SESSION_ID', '$PATIENT_ID', 'smoke-device', 'active', '[\"thyroid\"]'::jsonb, '{}'::jsonb, 'kb_v1.0.0', now(), now(), 'granted') ON CONFLICT (session_id) DO NOTHING" 2>&1 | tail -1
$PSQL -c "INSERT INTO consent_records (record_id, patient_id, status, granted_at, consent_version, created_at) SELECT 9000000000001, '$PATIENT_ID', 'granted', now(), '1.0', now() WHERE NOT EXISTS (SELECT 1 FROM consent_records WHERE record_id = 9000000000001)" 2>&1 | tail -1
echo "会话存在性: $($PSQL -c "select count(*) from sessions where session_id='$SESSION_ID'")"
echo "同意记录数: $($PSQL -c "select count(*) from consent_records where patient_id='$PATIENT_ID'")"

echo
echo "=== 3) 鉴权 + 提交最小诊断 + 轮询终态（自造文本，无真实患者数据）==="
# 诊断端点需要鉴权（实测 401 E1003），且 `users` 表是迁移新建的、初始为空 ——
# 建冒烟用户与登录都在探针进程内完成（明文口令不进 argv、不进仓库）。
"$UAI/venv/bin/python" "$HOME/server_smoke_probe.py"

echo
echo "=== 4) 本段日志事件计数（仅事件名与状态）==="
tr -d '\000' < "$UAI/logs/api.log" > "$UAI/logs/api.clean.log"
start=$(grep -an '===== RESTART' "$UAI/logs/api.clean.log" | tail -1 | cut -d: -f1)
sed -n "${start:-1},\$p" "$UAI/logs/api.clean.log" > "$UAI/logs/api.segment.log"
echo "--- error/critical ---"
grep -aoE '\[(error|critical) +\] [a-z_]+' "$UAI/logs/api.segment.log" | sort | uniq -c | sort -rn | head -10
echo "--- warning ---"
grep -aoE '\[warning +\] [a-z_]+' "$UAI/logs/api.segment.log" | sort | uniq -c | sort -rn | head -10

echo
echo "=== 说明：本冒烟只验证链路可达性与状态流转，**不产出任何数值结论** ==="
