#!/usr/bin/env bash
# 从 git archive 部署到服务器，使 服务器代码 == 本地 HEAD == 远程仓库。
#
# 为什么不 scp 单个文件：本地 main 曾与服务器 feature 分支不同谱系，
# 单文件覆盖会把对方独有的类冲掉（ClinicalAction 事件故启动即 ImportError）。
# 整树替换才能保证"三处完全相同"。
set -uo pipefail

TAR="${1:?usage: $0 <tar> <sha>}"
SHA="${2:?usage: $0 <tar> <sha>}"
U=/mnt/ee16/home/student03/uai
SRC=$U/src

echo "=== 1. 备份当前 src ==="
if [ -d "$SRC" ]; then
  BK="$U/src.bak.$(date +%Y%m%d_%H%M%S)"
  mv "$SRC" "$BK"
  echo "已备份到 $BK"
fi

echo "=== 2. 解包 ==="
mkdir -p "$SRC"
tar -xf "$TAR" -C "$SRC" || { echo "!! 解包失败"; exit 1; }
echo "文件数: $(find "$SRC" -type f | wc -l)"

echo "=== 3. 核对关键文件 ==="
for f in app/api/v1/session.py app/models/schemas.py app/repositories/session_repository.py; do
  if [ -f "$SRC/$f" ]; then echo "  OK $f"; else echo "  !! 缺失 $f"; fi
done
# ClinicalAction 是 feature 分支独有的类，曾被 main 版 schemas.py 覆盖掉，
# 导致 feature 的 protocols.py import 失败、启动即 ImportError —— 必须校验。
if grep -q "class ClinicalAction" "$SRC/app/models/schemas.py"; then
  echo "  OK ClinicalAction 存在"
else
  echo "  !! ClinicalAction 缺失 -- 打包源不对"; exit 1
fi
if grep -q "async def list_sessions" "$SRC/app/api/v1/session.py"; then
  echo "  OK 列表端点存在"
else
  echo "  !! 列表端点缺失"; exit 1
fi
echo "$SHA" > "$SRC/.deployed_sha"
echo "  .deployed_sha = $SHA"

echo "=== 4. 清 pycache 并重启 ==="
find "$SRC" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
bash ~/server_restart_all.sh > "$U/logs/deploy_restart.log" 2>&1
if grep -q "Application startup complete" "$U/logs/deploy_restart.log"; then
  echo "  启动成功"
else
  echo "  !! 启动失败，日志尾部："
  tr -d '\000' < "$U/logs/deploy_restart.log" | tail -25
  exit 1
fi

echo "=== 5. 健康检查 ==="
curl -s -o /dev/null -w "  main_health=%{http_code}\n" --max-time 20 http://127.0.0.1:18000/api/v1/health
curl -s -o /dev/null -w "  sessions_list=%{http_code}\n" --max-time 20 http://127.0.0.1:18000/api/v1/sessions
echo "=== done ==="
