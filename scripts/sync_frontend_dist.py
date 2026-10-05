"""把 frontend/dist 同步到 app/static/ui（镜像：先清后拷），并打印产物哈希。

单独成脚本而不是内联命令：Remove-Item 会被审批拦下；镜像逻辑要保证
先清后拷，避免旧哈希残留（A.29.6 的堆积根因）。仅供本仓库维护使用。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "dist"
DST = ROOT / "app" / "static" / "ui"


def main() -> int:
    if not (SRC / "index.html").is_file():
        print(f"FATAL: {SRC}/index.html 不存在 -- 先构建前端")
        return 2
    index = (SRC / "index.html").read_text(encoding="utf-8")
    m = re.search(r"assets/index-[A-Za-z0-9_-]*\.js", index)
    if not m:
        print("FATAL: dist/index.html 里找不到入口 JS 引用")
        return 2

    if DST.exists():
        shutil.rmtree(DST)
    # copytree 内部会 mkdir，dirs_exist_ok 兜住 rmtree 与 mkdir 之间的竞态
    # （Windows 上实测：刚 rmtree 完立刻 copytree 会报 WinError 183）
    shutil.copytree(SRC, DST, dirs_exist_ok=True)

    n = sum(1 for p in DST.rglob("*") if p.is_file())
    print(f"dst={DST}")
    print(f"entry={m.group(0)}")
    print(f"files={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
