"""交付/规格文档不得被 gitignore 覆盖（铁律 #9 的第二次复发）。

为什么需要这道守卫
-----------------
`test_test_files_are_tracked.py` 已防住"测试文件被吞"，但同一个手法在**文档**上
又咬了一次（2026-10-05 实测）：`.gitignore` 里原有
`docs-for-guiding generating docs/` 这条**目录级**忽略，于是

1. `git add` 对被 ignore 的路径**静默跳过**（除非 `-f`）-> 提交照样成功，文档
   本体从未进仓库。上一轮 `0352929 docs(交付): 18 号交付使用手册` 实际只入库了
   `scripts/sync_final.sh`，16 号附录 A.24.4 却按"已交付"记账。
2. 目录级排除**阻止 git 下探**，使逐级反白名单失效（同 `data/*` 的机制）。

后果与测试文件一样严重但更隐蔽：交付物缺失会一路带到 NMPA 验收，而 CI 全绿。

判定语义（踩过一次坑，别再写错）
--------------------------------
不能用"`git check-ignore` 是否命中"当"文档是否安全"的判据：
**check-ignore 只看规则、不看跟踪状态**。该目录 38 个 `.md` 里有 28 个是早期规则
出现前就已入库的历史文件，它们至今仍被新规则命中，但早已在仓库里、clone 出来
也在 —— 拿 check-ignore 全量判定会把 28 个正常文件误报成风险。

正确判据是：**要么已 tracked，要么不被忽略**（`ls-files --others
--exclude-standard` 为空）。本文件按此语义实现。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_GLOB = "docs-for-guiding generating docs"
DOCS_DIR = REPO_ROOT / DOCS_GLOB


def _run_git(args, *, stdin=""):
    # Windows 上 text=True 会把换行转成 CRLF 写进 stdin，git 回显每行都带尾随 CR；
    # `_lines()` 统一 strip 即可。`newline=` 是 TextIOWrapper 的参数，
    # subprocess.run **不接受**（踩过：TypeError unexpected keyword 'newline'）。
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, input=stdin,
        capture_output=True, text=True,
    )


def _git_usable():
    if shutil.which("git") is None:
        return False
    return _run_git(["rev-parse", "--is-inside-work-tree"]).returncode == 0


def _doc_markdown_files():
    if not DOCS_DIR.is_dir():
        return []
    return sorted(
        str(p.relative_to(REPO_ROOT)).replace("\\", "/") for p in DOCS_DIR.rglob("*.md")
    )


def _lines(completed):
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def git_repo():
    if not _git_usable():
        pytest.skip("当前环境没有可用的 git 仓库，跳过")


class TestNoIgnoredDocFiles:
    def test_no_untracked_doc_file(self, git_repo):
        """核心守卫：文档既没被跟踪、又被忽略 -> 交付物只存在于本机。

        这正是 `0352929 docs(交付): 18 号交付使用手册` 的死法：提交成功，
        手册本体被 `git add` 静默跳过，仓库与服务器上都没有。
        """
        files = _doc_markdown_files()
        assert len(files) > 20, "文档目录解析失效，后续断言会空转"
        untracked = [
            line for line in _lines(
                _run_git(["ls-files", "--others", "--exclude-standard", DOCS_GLOB])
            ) if line.endswith(".md")
        ]
        assert untracked == [], (
            "下列文档既未纳入版本控制、又被 .gitignore 命中（等于只存在于本机，"
            "交付时会缺）：\n  " + "\n  ".join(untracked)
        )

    def test_most_docs_are_tracked(self, git_repo):
        """反向自证：绝大多数文档必须在库里。

        没有这条，上一条可能因路径写错而恒空转 —— 空转的守卫比没有守卫更糟，
        因为它让人以为已经防住了。
        """
        total = len(_doc_markdown_files())
        tracked = set(_lines(_run_git(["ls-files", DOCS_GLOB])))
        assert len(tracked) >= total * 0.9, (
            f"仅 {len(tracked)}/{total} 个文档在版本库，覆盖率异常低，"
            "请确认不是路径或 ignore 规则又出问题"
        )

    def test_directory_level_ignore_is_gone(self, git_repo):
        """本轮修复的落点：目录级规则必须已删除。

        目录级排除会阻止 git 下探，使逐级反白名单**完全失效** —— 这正是
        第一次修法没生效的原因（反白名单加了，但 `git add` 仍报
        "paths are ignored"）。所以直接钉住"目录本身不再被忽略"。
        """
        completed = _run_git(["check-ignore", "--stdin"], stdin=f"{DOCS_GLOB}/")
        assert completed.returncode == 1, (
            f"{DOCS_GLOB}/ 仍是目录级忽略：反白名单会失效，"
            "文档将再次静默不入库（铁律 #9）"
        )

    def test_check_ignore_mechanism_works(self, git_repo):
        """`git check-ignore` 本身在工作（用 `data/` 反向验证）。

        若这里都不命中，上面那条 returncode 断言就没有判别力。
        """
        completed = _run_git(["check-ignore", "--stdin"], stdin="data/__probe_check__.md")
        assert completed.returncode == 0, (
            "git check-ignore 未命中本应被忽略的 data/ 路径，该守卫无法发现问题"
        )
