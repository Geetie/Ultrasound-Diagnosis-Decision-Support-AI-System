#!/usr/bin/env bash
# 前端产物部署（清空式）——A.52 摸排发现：手工 `Copy-Item dist/* app/static/ui/`
# 只做覆盖不做删除，历次构建的 hash 文件会**持续堆积**（实测积到 65 个 js，
# 单次构建只需 ~25 个），仓库里因此塞进几十个永不引用的 chunk。
#
# 用法（仓库根）：
#   bash scripts/deploy_frontend.sh
# 做三件事：① 构建（VITE_BASE=/ui/，SPA 挂在 /ui/ 下）② 清空 app/static/ui
# ③ 拷入新产物并打印数量；随后自行 `git add -A app/static/ui` 提交。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/frontend"

echo "== 1) 构建（VITE_BASE=/ui/）=="
export VITE_BASE=/ui/
npm run build

echo "== 2) 清空 app/static/ui =="
cd "$ROOT"
before=$(find app/static/ui -type f | wc -l)
rm -rf app/static/ui
mkdir -p app/static/ui

echo "== 3) 拷入新产物（清理前 $before 个文件）=="
cp -r frontend/dist/. app/static/ui/
after=$(find app/static/ui -type f | wc -l)
echo "app/static/ui 现 $after 个文件（js: $(find app/static/ui/assets -name '*.js' | wc -l)）"
echo "下一步：git add -A app/static/ui && git commit"
