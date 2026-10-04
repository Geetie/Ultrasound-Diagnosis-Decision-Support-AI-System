#!/usr/bin/env bash
# 交付前全链路回归（一条命令验全部）
# 覆盖：服务矩阵 / UI / 诊断链路 / 分割旁路 / 报告 / 签名链
set -u
cd ~/uai/src
set -a
source ~/uai/.env
set +a
export PYTHONPATH=~/uai/src
UAI=$HOME/uai
export NODULE_CONTOUR_IN_PIPELINE_ENABLED=1
export ATTRIBUTE_HINTS_ENABLED=1

PASS=0
FAIL=0
ok()   { PASS=$((PASS+1)); echo "  [PASS] $*"; }
bad()  { FAIL=$((FAIL+1)); echo "  [FAIL] $*"; }
check() { if [ "$1" = "$2" ]; then ok "$3 ($1)"; else bad "$3 (期望 $2，实得 $1)"; fi; }

echo "=== A. 服务矩阵 ==="
# ⚠️ 18000 的健康端点是 /api/v1/health，**不是** /health（写错会得到 404，
# 曾经两次把这个当"服务挂了"白查一轮——钉子，勿改回 /health）
for spec in "18000:/api/v1/health" "8002:/health" "8003:/health" "8006:/health" "9001:/health"; do
  p=${spec%%:*}
  ep=${spec#*:}
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "http://127.0.0.1:${p}${ep}" 2>/dev/null || echo 000)
  if [ "$c" = "200" ]; then ok "$p$ep 200"; else bad "$p$ep -> $c"; fi
done

echo "=== B. 依赖 ==="
curl -s --max-time 15 http://127.0.0.1:18000/api/v1/health \
  | grep -q '"status":"healthy"' && ok "主服务 healthy（含 DB/Redis/HSM）" || bad "主服务不健康"

echo "=== C. 前端 UI ==="
check "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:18000/ui/)" 200 "UI 根路径"
for sub in workspace report/x login; do
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:18000/ui/$sub")
  check "$c" 200 "SPA 子路由 $sub"
done
curl -s --max-time 10 http://127.0.0.1:18000/ui/ | grep -q 'id="root"' && ok "UI 入口含 root 容器" || bad "UI 入口异常"
check "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:18000/dashboard/)" 200 "运维仪表盘"

echo "=== D. 诊断链路（含分割旁路）==="
IMG=$(ls ~/uai/data/tn3k_dataset/test-image/* 2>/dev/null | head -1)
if [ -z "$IMG" ]; then
  bad "找不到演练用图"
else
  IMG_PATH="$IMG" ~/uai/venv/bin/python ~/uai/src/scripts/deploy_drill_e2e.py > /tmp/_drill_out.txt 2>&1
  rc=$?
  check "$rc" 0 "端到端演练（0=通过）"
  grep -q "E2E_DRILL_PASS" /tmp/_drill_out.txt && ok "演练标记 PASS" || bad "演练未 PASS"
  grep -q "area_mm2=" /tmp/_drill_out.txt && ok "分割输出 mm²" || bad "分割缺 mm²"
  grep -q "available=True" /tmp/_drill_out.txt && ok "分割旁路可用" || bad "分割旁路不可用"
fi

echo "=== E. 报告与签名链 ==="
rm -f /tmp/_rid_env.sh
~/uai/venv/bin/python $UAI/src/scripts/drill_pick_report.py >/dev/null 2>&1
if [ -f /tmp/_rid_env.sh ]; then
  RID=$(cut -d= -f2 /tmp/_rid_env.sh)
  TOK=$(~/uai/venv/bin/python $UAI/src/scripts/drill_token.py 2>/dev/null | tr -d '\r\n')
  curl -s --max-time 90 -X POST -H "Authorization: Bearer $TOK" \
    -H "Content-Type: application/json" -d '{}' \
    "http://127.0.0.1:18000/api/v1/report/$RID/sign" > /tmp/_sign.json 2>&1
  grep -q '"signature_present":true' /tmp/_sign.json && ok "报告签名成功" || bad "报告签名失败"
  curl -s --max-time 90 -X POST -H "Authorization: Bearer $TOK" \
    -H "Content-Type: application/json" -d '{}' \
    "http://127.0.0.1:18000/api/v1/report/$RID/verify" > /tmp/_verify.json 2>&1
  grep -q '"signature_verified":true' /tmp/_verify.json && ok "验签通过" || bad "验签失败"
  ~/uai/venv/bin/python $UAI/src/scripts/drill_tamper_check.py "$RID" 2>&1 | grep -q TAMPER_DETECTED \
    && ok "篡改检测生效" || bad "篡改未被发现（严重）"
else
  bad "找不到可签报告"
fi

echo "=== F. AI 能力矩阵 ==="
curl -s --max-time 30 http://127.0.0.1:18000/api/v1/segmentation/organs \
  | grep -q '"cardiac".*"trained"' && ok "心脏分割已训练" || bad "心脏分割状态异常"
curl -s --max-time 30 http://127.0.0.1:18000/api/v1/attributes/health \
  | grep -q '"available":true' && ok "属性提示可用（英文）" || bad "属性提示不可用"

echo "=== G. LLM 健康（交付头号历史阻塞）==="
# ⚠️ 别写 `grep -c ... || echo 0`：grep 自己在"零匹配"时也返回 0 退出码，
# 于是输出变成 "0\n0"，字符串比较必然不等 → 假 FAIL（踩过一次）
n=$(grep -c "generation_failed" /tmp/uvicorn18000.log 2>/dev/null | head -1)
n=${n:-0}
[ "$n" = "0" ] && ok "本轮无 LLM 生成失败" || bad "LLM 生成失败 $n 次"

echo "=== H. 迁移版本 ==="
cur=$(PYTHONPATH=~/uai/src ~/uai/venv/bin/alembic current 2>/dev/null | grep -oE "0017" | head -1)
check "${cur:-none}" "0017" "数据库迁移到 0017"

echo ""
echo "================================"
echo "PASS=$PASS FAIL=$FAIL"
echo "================================"
[ "$FAIL" = "0" ] || exit 1
