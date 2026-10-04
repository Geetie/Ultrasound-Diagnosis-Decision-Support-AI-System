#!/usr/bin/env bash
# 报告签名链验证：draft -> sign -> verify -> 篡改检测 -> 重复签
# 纪律：脚本自身必须 LF（scp 上传不会去 CRLF，CRLF 会让 [ -f ] 之后的行解析异常，
#      表现为"文件明明生成了却报 NO_REPORT"——已踩过一次）
set -u
cd ~/uai/src
set -a
source ~/uai/.env
set +a
export PYTHONPATH=~/uai/src

say() { echo "[$(date +%H:%M:%S)] $*"; }

say "=== 0) 定位可签报告 ==="
rm -f /tmp/_rid_env.sh
~/uai/venv/bin/python $UAI/src/scripts/drill_pick_report.py 2>&1 | tail -2
if [ ! -f /tmp/_rid_env.sh ]; then
  say "  NO_REPORT_YET"
  exit 2
fi
RID=$(cut -d= -f2 /tmp/_rid_env.sh)
say "  report_id=$RID"

say "=== 1) 登录 ==="
TOK=$(~/uai/venv/bin/python $UAI/src/scripts/drill_token.py 2>/dev/null | tr -d '\r\n')
if [ -z "$TOK" ]; then
  say "  LOGIN_FAIL"
  exit 2
fi
say "  token ok"

say "=== 2) 签名前状态 ==="
curl -s --max-time 20 -H "Authorization: Bearer $TOK" \
  "http://127.0.0.1:18000/api/v1/report/$RID" \
  | ~/uai/venv/bin/python -c "import json,sys;d=json.load(sys.stdin);print('  status=',d.get('status'),'signed=',d.get('signature_present'),'verified=',d.get('signature_verified'))"

say "=== 3) 签名 ==="
curl -s --max-time 90 -X POST -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" -d '{}' \
  "http://127.0.0.1:18000/api/v1/report/$RID/sign" \
  | ~/uai/venv/bin/python -c "import json,sys;d=json.load(sys.stdin);print('  status=',d.get('status'),'signed=',d.get('signature_present'),'digest=',(d.get('content_digest') or '')[:20])"

say "=== 4) 验签 ==="
curl -s --max-time 90 -X POST -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" -d '{}' \
  "http://127.0.0.1:18000/api/v1/report/$RID/verify" \
  | ~/uai/venv/bin/python -c "import json,sys;d=json.load(sys.stdin);print('  verified=',d.get('signature_verified'),'status=',d.get('status'))"

say "=== 5) 篡改检测（核心：改了内容签名必须失效）==="
~/uai/venv/bin/python $UAI/src/scripts/drill_tamper_check.py "$RID" 2>&1 | tail -4

say "=== 6) 重复签名 ==="
curl -s -o /dev/null -w "  re-sign http=%{http_code}\n" --max-time 60 -X POST \
  -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" -d '{}' \
  "http://127.0.0.1:18000/api/v1/report/$RID/sign"

say "DONE"
