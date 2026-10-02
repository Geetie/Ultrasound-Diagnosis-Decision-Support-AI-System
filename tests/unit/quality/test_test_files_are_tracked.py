"""测试代码不得被 gitignore 覆盖，也不得有"写完没提交"的测试文件（quality-debt）。

为什么需要这道守卫
------------------
实测踩到过一次，代价很大：`.gitignore` 里的无锚点模式 `data/` 把
`tests/unit/data` 与 `tests/integration/data` **整目录**忽略了，于是 3 个测试文件
只存在于本机、**CI 从未运行过它们**（`test_desensitization.py` 还是 31KB 的主要测试）。
后果是本地覆盖率虚高于 CI —— 我正好在那一轮把覆盖率门禁从 79 提到 80，差点让 CI
红在一个"本地明明达标"的数字上。

这类问题人工发现成本高（要恰好 `git add` 撞上 "paths are ignored" 才报错），
所以机器化：
1. `tests/**` 下的 `.py` 不得被 gitignore 命中；
2. `tests/**` 下不得存在未跟踪（且未被忽略）的 `.py` —— 即"写了测试但忘了 git add"。
   同一晚我在根目录脚本上也犯过一次同类疏漏，只是那次被自查发现。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TESTS_DIR = REPO_ROOT / "tests"


def _run_git(args: list, *, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        input=stdin,
        capture_output=True,
        text=True,
    )


def _git_usable() -> bool:
    if shutil.which("git") is None:
        return False
    return _run_git(["rev-parse", "--is-inside-work-tree"]).returncode == 0


def _test_python_files() -> list:
    return sorted(
        str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        for path in TESTS_DIR.rglob("*.py")
    )


@pytest.fixture(scope="module")
def git_repo() -> None:
    if not _git_usable():
        pytest.skip("当前环境没有可用的 git 仓库，跳过")


class TestNoIgnoredTestFiles:
    def test_python_files_found(self):
        """解析失效会让下面的断言空转，先自证。"""
        assert len(_test_python_files()) > 50

    def test_no_test_file_is_ignored(self, git_repo):
        """被 ignore 的测试文件不会进仓库 -> CI 根本不会运行它。"""
        completed = _run_git(["check-ignore", "--stdin"], stdin="\n".join(_test_python_files()))

        # check-ignore：命中返回 0 并打印路径，未命中返回 1
        ignored = [line for line in completed.stdout.splitlines() if line.strip()]

        assert ignored == [], (
            "下列测试文件被 gitignore 命中，它们不会进入仓库、CI 也不会运行：\n  "
            + "\n  ".join(ignored)
        )

    def test_no_untracked_test_file(self, git_repo):
        """写了测试却忘了 git add：本地绿、CI 里这批测试根本不存在。"""
        completed = _run_git(["ls-files", "--others", "--exclude-standard", "tests"])

        untracked = [
            line for line in completed.stdout.splitlines() if line.strip().endswith(".py")
        ]

        assert untracked == [], (
            "下列测试文件尚未纳入版本控制（记得 git add，否则 CI 不会运行）：\n  "
            + "\n  ".join(untracked)
        )

    def test_guard_mechanism_actually_works(self, git_repo):
        """反向自证：`git check-ignore` 必须认得出"应被忽略"的路径。

        用仓库根的 `data/`（仍在忽略范围内）验证。若这里都不命中，说明 check-ignore
        没在工作，那么上面"没有测试文件被忽略"的断言就是空转 —— 空转的守卫比没有守卫
        更糟，因为它会让人以为已经防住了。
        """
        completed = _run_git(["check-ignore", "--stdin"], stdin="data/__probe_check__.py")

        assert completed.returncode == 0, (
            "git check-ignore 未命中本应被忽略的 data/ 路径，说明该守卫无法发现问题"
        )

    def test_negation_rule_keeps_test_data_dirs_visible(self, git_repo):
        """本轮修复的落点：tests/unit/data 必须已被放行，不再被目录级规则吞掉。"""
        completed = _run_git(
            ["check-ignore", "--stdin"], stdin="tests/unit/data/__probe_check__.py"
        )

        assert completed.returncode == 1, (
            "tests/unit/data 仍在忽略范围内：测试文件会再次只存在于本机"
        )
