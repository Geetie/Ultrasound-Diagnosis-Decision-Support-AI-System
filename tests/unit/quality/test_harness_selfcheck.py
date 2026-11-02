"""A.106 守卫：harness 自检（p78）必须"红得准、绿得可信"。

为什么需要这组测试：`p78_harness_selfcheck.py` 是"整饬 harness"的**唯一机器判据**
（用户级 `validate-harness.mjs` 在本机不存在）。判据本身若会漏报，用它背书过的
结论就不可信——所以既钉"真仓库全绿"，也钉"故意坏掉的仓库必须红"。

三条纪律：
* 状态文件缺失/不可解析/关键节缺失/死链 → **FAIL**（本轮可修，必须阻断）；
* 历史遗留（附录重号、编号缺口）与需人拍板（`in_progress` > 1）→ **WARN**，不阻断；
* 纯文件名引用（如 `settings.py`）允许写任意层级，按 git 清单做后缀匹配，不许假红。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "scripts" / "audit" / "p78_harness_selfcheck.py"


def _load():
    spec = importlib.util.spec_from_file_location("_p78_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclass 处理期要按 __module__ 回查 sys.modules（3.12），不入表会报
    # AttributeError: 'NoneType' object has no attribute '__dict__'
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _mk_repo(tmp_path: Path) -> Path:
    """最小可用 harness 仓库（全部检查都应为绿）。"""
    p78 = _load()
    root = tmp_path
    _write(
        root / "AGENTS.md",
        "# 入口\n\n## 权威次序\n\n## Definition of Done\n\n## Verification Commands\n\n## 硬规则\n",
    )
    _write(root / "progress.md", "## Last Updated\n\n## Blockers\n\n## Recommended Next Step\n")
    _write(
        root / "session-handoff.md",
        "\n".join(
            f"## {m}" for m in ("Last Updated", "Current Objective", "Recommended Next Step", "Blockers", "Files", "Next Session")
        ),
    )
    _write(root / "init.sh", "#!/usr/bin/env bash\ntrue\n")
    _write(
        root / "feature_list.json",
        json.dumps({"features": [{"id": "x", "name": "x", "status": "planned"}]}, ensure_ascii=False, indent=2) + "\n",
    )
    _write(root / p78.PROGRESS_FILE, "### A.1 首条\n\n### A.2 次条\n")
    return root


def _fails(results) -> list[str]:
    return [r.name for r in results if not r.ok and r.level == "FAIL"]


def _warns(results) -> list[str]:
    return [r.name for r in results if not r.ok and r.level == "WARN"]


def test_real_repo_has_no_fail():
    """真仓库不得有 FAIL（WARN 允许：历史附录编号、需拍板的 in_progress）。"""
    p78 = _load()
    results = p78.run(_REPO_ROOT)
    assert p78.main(["--root", str(_REPO_ROOT)]) == 0
    assert _fails(results) == [], f"真仓库有 FAIL：{_fails(results)}"


def test_missing_required_file_is_fail(tmp_path):
    p78 = _load()
    root = _mk_repo(tmp_path)
    (root / "session-handoff.md").unlink()
    results = p78.run(root)
    assert "file:session-handoff.md" in _fails(results)


def test_unparseable_feature_list_is_fail(tmp_path):
    p78 = _load()
    root = _mk_repo(tmp_path)
    _write(root / "feature_list.json", "{ not json")
    assert "feature_list" in _fails(p78.run(root))


def test_two_in_progress_is_warn_not_fail(tmp_path):
    """AGENTS「同一时间只推进一个」违反要报告，但不阻断本轮（需人拍板）。"""
    p78 = _load()
    root = _mk_repo(tmp_path)
    _write(
        root / "feature_list.json",
        json.dumps(
            {
                "features": [
                    {"id": "a", "name": "a", "status": "in_progress"},
                    {"id": "b", "name": "b", "status": "in_progress"},
                ]
            },
            ensure_ascii=False,
        ),
    )
    results = p78.run(root)
    assert "feature_list:one_in_progress" in _warns(results)
    assert "feature_list:one_in_progress" not in _fails(results)


def test_appendix_gap_and_dup_are_warn(tmp_path):
    p78 = _load()
    root = _mk_repo(tmp_path)
    _write(root / p78.PROGRESS_FILE, "### A.1 首条\n\n### A.2 次条\n\n### A.2 重号\n\n### A.4 跳号\n")
    results = p78.run(root)
    assert "appendix:A.N" in _warns(results)
    assert "appendix:A.N" not in _fails(results)


def test_agents_dead_link_is_fail(tmp_path):
    p78 = _load()
    root = _mk_repo(tmp_path)
    _write(
        root / "AGENTS.md",
        "## 权威次序\n\n## Definition of Done\n\n## Verification Commands\n\n## 硬规则\n\n见 `docs/nope/missing.md`\n",
    )
    (root / ".git").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=False)
    assert "agents:links" in _fails(p78.run(root))


def test_bare_filename_link_resolves_by_suffix(tmp_path):
    """`settings.py` 这种纯文件名：仓库里任意层级有就算可解析（不许假红）。"""
    p78 = _load()
    root = _mk_repo(tmp_path)
    _write(root / "AGENTS.md", "## 权威次序\n\n## Definition of Done\n\n## Verification Commands\n\n## 硬规则\n\n见 `settings.py`\n")
    _write(root / "app" / "settings.py", "# x\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=False)
    subprocess.run(["git", "add", "-A"], cwd=root, check=False)
    results = p78.run(root)
    assert "agents:links" not in _fails(results)


@pytest.mark.parametrize("name", ["file:AGENTS.md", "feature_list", "progress:markers", "handoff:markers"])
def test_checks_are_enumerated(name):
    """检查项名字是审计方写报告时的锚点，改名要被这个用例拦下。"""
    p78 = _load()
    names = {r.name for r in p78.run(_REPO_ROOT)}
    assert name in names
