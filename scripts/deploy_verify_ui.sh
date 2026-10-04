#!/usr/bin/env bash
# 部署前端产物 + 重启 + 验 /ui 与 SPA 回落 + 回归关键端点
set -u
cd ~/uai/src
set -a; source ~/uai/.env; set +a
say() { echo "[$(date +%H:%M:%S)] $*"; }

say "=== 1) 产物是否随包到达 ==="
if [ -d ~/uai/src/app/static/ui ]; then
  say "  ui files: $(find ~/uai/src/app/static/ui -type f | wc -l), size: $(du -sh ~/uai/src/app/static/ui | cut -f1)"
else
  say "  MISSING app/static/ui"
  exit 2
fi

say "=== 2) 重启主服务 ==="
pkill -9 -f "uvicorn app.main:app --host 0.0.0.0 --port 18000" 2>/dev/null || true
sleep 3
export CUDA_VISIBLE_DEVICES=0
export NODULE_CONTOUR_IN_PIPELINE_ENABLED=1
setsid nohup ~/uai/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18000 \
  --log-level warning > /tmp/uvicorn18000.log 2>&1 < /dev/null &
disown
for i in $(seq 1 40); do
  sleep 3
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:18000/api/v1/health 2>/dev/null)
  [ "$c" = "200" ] && { say "  18000 OK ($((i*3))s)"; break; }
done

say "=== 3) UI 端点 ==="
for ep in /ui/ /ui/index.html /dashboard/ /; do
  printf "  %-18s %s\n" "$ep" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:18000${ep}")"
done

say "=== 4) SPA 回落（子路由应回 index.html 而不是 404）==="
for ep in /ui/workspace /ui/report/abc /ui/login; do
  code=$(curl -s -o /tmp/_ui.html -w "%{http_code}" --max-time 10 "http://127.0.0.1:18000${ep}")
  has=$(grep -c "<div id=\"root\">" /tmp/_ui.html 2>/dev/null || echo 0)
  printf "  %-22s http=%s root_div=%s\n" "$ep" "$code" "$has"
done

say "=== 5) 静态资源（MIME 与缓存）==="
asset=$(curl -s --max-time 10 http://127.0.0.1:18000/ui/ | grep -oE '/ui/assets/index-[A-Za-z0-9_-]+\.js' | head -1)
say "  entry: ${asset:-none}"
[ -n "$asset" ] && curl -s -o /dev/null -w "  asset http=%{http_code} type=%{content_type}\n" --max-time 20 "http://127.0.0.1:18000${asset}"

say "=== 6) 关键 API 回归 ==="
for ep in /api/v1/health /api/v1/segmentation/organs /api/v1/attributes/health /openapi.json; do
  printf "  %-30s %s\n" "$ep" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "http://127.0.0.1:18000${ep}")"
done
say "DONE"
