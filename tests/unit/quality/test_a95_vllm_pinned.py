"""A.95 Q33：vLLM 安装脚本**必须钉死版本**（15 号 P1 第 5 项）。

## 为什么这条值得单独守

15 号 P1 原话：「vLLM：~~升级至当前稳定版并固定版本号~~ → 已切换为官方 OpenAI server…
**待办：版本号固定**（现仍是浮动版本，违反『27 项精确锁定』）」。

原安装脚本是 `pip install vllm`（无版本约束）。后果：

* **推理行为可能静默变化** —— 采样、结构化输出（`response_format`）、显存占用
  在不同 vLLM 版本间都有变化，而这些东西**不会报错，只会悄悄不一样**；
* **历史评测不可复现** —— "上个月 AUC 0.89" 可能是另一个 vLLM 跑出来的，
  而你无法知道是哪个；
* 与仓库其它 27 项精确锁定不一致，形成"部分锁定"的假象。

真机实测版本：**0.30.0**。

## 本守卫钉什么

1. 脚本里不许出现无版本的 `pip install vllm`；
2. 必须有 `VLLM_VERSION` 默认值，且是精确三段式；
3. 安装命令必须是 `vllm==$VLLM_VERSION`；
4. 自检段必须**比对**装到的版本与期望版本（不一致要退出非零）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.track_ops]

_REPO = Path(__file__).resolve().parents[3]
_INSTALL = _REPO / "scripts" / "server_install_vllm_isolated.sh"


class TestVllmVersionPinned:
    def _src(self) -> str:
        return _INSTALL.read_text(encoding="utf-8")

    def test_no_unversioned_install(self):
        """反证腿：把 `vllm==$VLLM_VERSION` 改回裸 `vllm` 即红。"""
        src = self._src()
        # 找所有 pip install 行里的 vllm 引用
        bad = []
        for line in src.splitlines():
            s = line.strip()
            if s.startswith("#") or "pip install" not in s:
                continue
            if re.search(r"\bvllm\b(?!==)", s) and "vllm==" not in s:
                bad.append(s)
        assert not bad, (
            "存在**无版本**的 vllm 安装（版本会浮动，推理行为与评测不可复现）:\n"
            + "\n".join(f"  {b}" for b in bad)
        )

    def test_default_version_is_exact_semver(self):
        src = self._src()
        m = re.search(r'VLLM_VERSION="\$\{VLLM_VERSION:-([^}]+)\}"', src)
        assert m, "没有 VLLM_VERSION 默认值（无法复现安装）"
        ver = m.group(1)
        assert re.fullmatch(r"\d+\.\d+\.\d+", ver), (
            f"VLLM_VERSION 默认值不是精确三段式: {ver!r}"
        )

    def test_install_uses_pinned_form(self):
        src = self._src()
        assert 'vllm==$VLLM_VERSION' in src, (
            "安装命令不是 `vllm==$VLLM_VERSION` —— 精确钉死是这条的全部意义"
        )

    def test_selfcheck_compares_expected(self):
        """自检必须比对版本，不一致时退出非零（否则装错版本也静默通过）。"""
        src = self._src()
        assert "EXPECTED" in src, "自检没有拿期望版本做比对"
        assert "sys.exit(1)" in src, "版本不一致时没有退出非零"
        assert "MISMATCH" in src, "版本不一致时没有明确报错信息"

    def test_default_matches_production_reality(self):
        """默认值应与真机实装一致（0.30.0）—— 防止写一个从未验证过的版本号。"""
        src = self._src()
        m = re.search(r'VLLM_VERSION="\$\{VLLM_VERSION:-([^}]+)\}"', src)
        assert m and m.group(1) == "0.30.0", (
            f"默认版本 {m.group(1) if m else '?'} 与真机实测 0.30.0 不一致；"
            "改默认值前必须先真机验证新版本"
        )
