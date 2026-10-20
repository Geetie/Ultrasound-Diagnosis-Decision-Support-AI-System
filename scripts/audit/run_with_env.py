"""带环境启动器：注入 `~/uai/.env` 后运行 `src/` 下的任意脚本（供 cron / 手工调用）。

**为什么需要**：cron 不加载登录 shell 环境，也不认 fish 的 `.env` 习惯；
而本项目的脚本一律 `from app.config.settings import settings`（要 `DATABASE_URL` 等）。
直接写 `python scripts/audit/xxx.py` 进 crontab 会因缺环境变量失败 —— 且 cron 里失败**没人看**。

用法：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/audit/p51_daily_quality_monitor.py --days 1

约定：**第一个参数是相对 `~/uai/src` 的脚本路径**，其余参数原样转发。
"""

from __future__ import annotations

import os
import pathlib
import runpy
import sys

SRC = pathlib.Path.home() / "uai" / "src"
ENV_FILE = pathlib.Path.home() / "uai" / ".env"


def load_env(path: pathlib.Path = ENV_FILE) -> int:
    """把 dotenv 注入 `os.environ`（不覆盖已有变量），返回注入条数。纯函数（可测）。"""
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")
            count += 1
    return count


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：run_with_env.py <相对 src 的脚本路径> [参数...]")
        return 2
    injected = load_env()
    os.chdir(SRC)
    sys.path.insert(0, str(SRC))
    target = SRC / sys.argv[1]
    if not target.is_file():
        print(f"[FATAL] 脚本不存在：{target}（注入了 {injected} 个环境变量）")
        return 2
    sys.argv = [target.name, *sys.argv[2:]]
    try:
        runpy.run_path(str(target), run_name="__main__")
    except SystemExit as exc:
        print("exit:", exc.code)
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
