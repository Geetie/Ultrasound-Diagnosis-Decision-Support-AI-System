#!/usr/bin/env bash
# 决定性实验：SG 到底认不认 output_data.diagnosis_data.patient_conditions？
# 发两次同样的 /api/v1/safety/check，只有 diagnosis_data 有无之别，然后数日志。
set -u
UAI="$HOME/uai"
SG=http://127.0.0.1:9001
LOG="$UAI/logs/sg.log"

count_no_condition() {
  tr -d '\000' < "$LOG" 2>/dev/null | grep -ac 'contraindication_no_condition_data' || true
}

BEFORE=$(count_no_condition)
echo "实验前 no_condition_data 计数: $BEFORE"

echo
echo "=== A) 不带 diagnosis_data ==="
curl -s -o /tmp/sg_a.json -w 'http=%{http_code}\n' --max-time 20 -X POST "$SG/api/v1/safety/check" \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"smoke-session-0001","patient_id":"smoke-patient-0001","output_data":{"organ":"thyroid","findings":"低回声结节","recommendation":"随访"},"consent_status_at_input":"granted"}'
sleep 2
AFTER_A=$(count_no_condition)
echo "  计数变化: $BEFORE -> $AFTER_A（应增加）"
head -c 200 /tmp/sg_a.json; echo

echo
echo "=== B) 带 diagnosis_data.patient_conditions=[] ==="
curl -s -o /tmp/sg_b.json -w 'http=%{http_code}\n' --max-time 20 -X POST "$SG/api/v1/safety/check" \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"smoke-session-0001","patient_id":"smoke-patient-0001","output_data":{"organ":"thyroid","findings":"低回声结节","recommendation":"随访","diagnosis_data":{"patient_conditions":[]}},"consent_status_at_input":"granted"}'
sleep 2
AFTER_B=$(count_no_condition)
echo "  计数变化: $AFTER_A -> $AFTER_B（**应保持不变**才说明 SG 认这个键）"
head -c 260 /tmp/sg_b.json; echo

echo
echo "=== 结论判据 ==="
if [ "$AFTER_B" = "$AFTER_A" ]; then
  echo "  SG 正确读取 diagnosis_data → 问题在应用侧发出的 payload"
else
  echo "  SG 未读取 diagnosis_data → 问题在 SG 侧（阶段取值或键名）"
fi
echo "=== done ==="
