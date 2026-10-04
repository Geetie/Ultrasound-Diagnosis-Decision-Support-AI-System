"""守卫：主服务绑定地址不得漂移出 loopback。

为什么这条要单独守（2026-10-05 真机事故）：
    AGENTS.md 硬规则 #3 要求「服务只绑 127.0.0.1，不许暴露到校园网」，
    但 `ss -ltn` 实测是 `0.0.0.0:18000` —— 临床 API 与真实患者检查链路
    对整个校园网裸奔。根因是仓库里有两套互相矛盾的启动命令：
    `_sync_apply.sh` / `_restart_main_std.sh` 写 127.0.0.1，另有 7 个
    `deploy_*.sh` 写死 0.0.0.0，没有唯一权威入口，谁最后跑谁说了算。

所以这里锁三件事：
    1. `Settings.api_host` 的默认必须是 loopback；
    2. 权威启动脚本 `scripts/start_main.sh` 必须绑 127.0.0.1，且脚本里
       出现 0.0.0.0 的地方只能是「禁止/说明」语境（防手滑改回）；
    3. 容器 `Dockerfile.dev` 必须仍绑 0.0.0.0 —— 这是**反向**锁定，
       防止有人"顺手统一"把它改成 loopback，导致 compose 的
       `ports: 8000:8000` 映射彻底不通（容器内自指 localhost）。
"""
from __future__ import annotations

import pathlib
import re

from app.config.settings import Settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class TestApiHostDefault:
    def test_default_is_loopback(self):
        """裸机默认必须是 loopback —— 这是硬规则 #3 的代码侧落点。"""
        assert Settings().api_host in LOOPBACK_HOSTS

    def test_container_override_still_possible(self):
        """容器场景需要显式 0.0.0.0，必须仍可覆盖（Dockerfile 走命令行 --host，
        但保留可覆盖性，避免有人把字段删了导致编排无处传参）。"""
        assert Settings(api_host="0.0.0.0").api_host == "0.0.0.0"


class TestAuthoritativeStartScript:
    """scripts/start_main.sh 是裸机唯一权威启动入口（2026-10-05 新增）。"""

    @staticmethod
    def _text() -> str:
        path = REPO_ROOT / "scripts" / "start_main.sh"
        assert path.is_file(), f"权威启动脚本缺失：{path}"
        return path.read_text(encoding="utf-8")

    def test_binds_loopback(self):
        text = self._text()
        assert re.search(r"--host\s+127\.0\.0\.1", text), "start_main.sh 必须绑 127.0.0.1"

    def test_no_wildcard_bind_in_active_command(self):
        """脚本里唯一允许的 `--host 0.0.0.0` 只能出现在 Dockerfile 注释里，
        不能出现在真实的 uvicorn 命令行（否则等于漂回裸机暴露）。"""
        text = self._text()
        # 去掉注释行后再找活动命令
        code_lines = [
            ln for ln in text.splitlines()
            if not ln.lstrip().startswith("#") and "--host" in ln
        ]
        for ln in code_lines:
            assert "0.0.0.0" not in ln, f"活动命令里出现 0.0.0.0：{ln.strip()}"

    def test_line_endings_are_lf(self):
        """服务器是 fish/bash，CRLF 会让 shebang 与续行解析直接炸
        （2026-10-03 踩过：108 个 shell 脚本是 CRLF）。"""
        raw = (REPO_ROOT / "scripts" / "start_main.sh").read_bytes()
        assert b"\r\n" not in raw, "start_main.sh 必须是 LF 换行"


class TestContainerBindingNotUnified:
    """反向锁定：容器内必须 0.0.0.0，别被"顺手统一"成 loopback。"""

    def test_dockerfile_dev_binds_wildcard(self):
        text = (REPO_ROOT / "Dockerfile.dev").read_text(encoding="utf-8")
        assert '"--host", "0.0.0.0"' in text, (
            "Dockerfile.dev 必须在容器内绑 0.0.0.0，"
            "否则 compose 的 ports: 8000:8000 映射不通"
        )
