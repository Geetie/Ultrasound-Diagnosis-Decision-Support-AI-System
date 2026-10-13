"""零静默守卫（约定层 · 前端）：驱动 `local/no-silent-catch` 棘轮（A.58）。

为什么前端单独一条规则 + 独立 config
------------------------------------
20 号对"静默失败"的定义是「异常被吞掉/降级为占位，而医生界面看起来一切正常」；
前端最典型的形态是 **A.35**：影像层三处 ``catch`` 链吞掉 chunk 求值失败，
页面空白活了 3 天，而所有门禁都是绿的。约定层要求新写的 ``catch`` 必须
「留痕 / 继续抛出 / 用户可见降级」三选一（或显式 ``// silent-ok: 理由`` 豁免）。

实现放在前端（ESLint 本地规则 + 基线棘轮，见 ``frontend/eslint-rules/``），
本文件只做**驱动与反证**：

* ``test_ratchet_passes``：棘轮通过（存量 ≤ 基线，无新增）；
* ``test_ratchet_bites_on_new_silent_catch``：**反证**——往 ``src/`` 放一个
  静默 ``catch``，棘轮必须判红（否则这个守卫就是"永远打印通过"的假守卫）。

node 不可用时**显式失败**（不 skip）：这条门禁是约定层的一部分，静默跳过等于没有。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND = REPO_ROOT / "frontend"
RATCHET = FRONTEND / "eslint-rules" / "silent-catch-ratchet.mjs"
BASELINE = FRONTEND / "eslint-rules" / "no-silent-catch.baseline.json"
PROBE = FRONTEND / "src" / "__silent_catch_probe.ts"


def _node() -> str:
    node = shutil.which("node")
    assert node, (
        "找不到 node —— 前端静默 catch 棘轮是约定层门禁的一部分，"
        "不允许静默跳过；请在具备 node 的环境跑本用例"
    )
    return node


def _run_ratchet() -> subprocess.CompletedProcess[str]:
    """跑棘轮。**显式 UTF-8 解码**：node 的输出是中文，Windows 下 `text=True` 会按
    本地代码页（GBK）解码并抛 UnicodeDecodeError —— 那会让本用例"因为读不到输出"
    而误判（A.58 实测踩到）。"""
    return subprocess.run(
        [_node(), str(RATCHET)],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )


def test_rule_and_config_are_wired():
    """规则/配置/基线的存在性与接线（防止有人"顺手删掉"其中一环）。"""
    assert RATCHET.is_file(), "缺棘轮脚本 eslint-rules/silent-catch-ratchet.mjs"
    assert (FRONTEND / "eslint-rules" / "no-silent-catch.js").is_file(), "缺本地规则实现"
    assert (FRONTEND / "eslint.silent-catch.config.js").is_file(), "缺独立 ESLint 配置"
    assert BASELINE.is_file(), "缺静默 catch 基线 JSON"
    config = (FRONTEND / "eslint.silent-catch.config.js").read_text(encoding="utf-8")
    assert "local/no-silent-catch" in config, "独立配置没有启用 local/no-silent-catch"
    pkg = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    scripts = pkg.get("scripts") or {}
    assert "lint:silent-catch" in scripts, "package.json 缺 lint:silent-catch 脚本"
    # 严格预算不能被这条规则污染（独立 config 的初衷）
    assert scripts.get("lint") == "eslint . --max-warnings 1", (
        "`npm run lint` 的严格预算被改动了 —— 静默 catch 必须走独立 config + 棘轮"
    )
    counts = json.loads(BASELINE.read_text(encoding="utf-8")).get("counts")
    # A.60 更正：存量已**全部归零**，因此 counts 允许为空 —— 空基线不是"棘轮退化"：
    # 未登记文件默认 0（新代码一律不许静默），而"到底会不会咬"由下面的反证用例
    # （test_ratchet_bites_on_new_silent_catch）保证。A.58 当初写「counts 非空」，
    # 那是"还有存量"阶段的前提，现在会误报。
    assert isinstance(counts, dict), "基线 counts 必须是对象（文件 → 静默 catch 数）"
    assert all(isinstance(v, int) and v > 0 for v in counts.values()), (
        "基线计数必须为正整数（0 值条目应删除，否则棘轮会在 0 与 0 之间空转）"
    )


class TestZeroSilenceDispositions:
    """A.60：存量归零时**逐处处置**留下的关键出口必须钉住（否则会被"顺手简化"掉）。

    这三条对应 A.35「影像层静默失败活 3 天」的真正病灶，A.60 修复后立此存照：
    ① 统一上报出口存在（散落的 `console.error` 无法接采集、无法统一文案）；
    ② `displayImage` 的异步链**重新抛出**（原先吞在 Promise 链里，上层
       `unhandledrejection` 监听接不到 → 空白影像区零提示）；
    ③ 影像/标注失败在**界面上**有出口（有上报没出口 = 医生照样不知道）。
    """

    SRC = REPO_ROOT / "frontend" / "src"

    def test_error_report_module_exists(self):
        text = (self.SRC / "utils" / "errorReport.ts").read_text(encoding="utf-8")
        for symbol in ("export function reportError", "export function reportWarn",
                       "export function registerErrorSink"):
            assert symbol in text, f"统一上报出口缺 {symbol}"

    def test_display_image_rethrows_so_ui_can_see_it(self):
        text = (self.SRC / "components" / "dicom" / "cornerstone.ts").read_text(
            encoding="utf-8"
        )
        assert "reportError('cornerstone3d'" in text, (
            "displayImage 的失败链必须先上报（A.35 病灶：原先只打一行 console.error）"
        )
        assert re.search(r"reportError\('cornerstone3d'.*?\n\s*throw err;", text, re.S), (
            "displayImage 的失败链必须**重新抛出**，否则 WorkspaceDicomLayer 的 "
            "unhandledrejection 监听接不到 → 空白影像区零提示（A.35 形态）"
        )

    def test_failure_outlets_exist_in_ui(self):
        layer = (
            self.SRC / "components" / "workspace" / "WorkspaceDicomLayer.tsx"
        ).read_text(encoding="utf-8")
        assert 'data-region="dicom-load-error"' in layer, "影像渲染失败的可读出口不见了"
        assert 'data-region="annotation-archive-error"' in layer, (
            "标注存档失败必须有可读出口（A.60：原先完全无声，医生以为已存上）"
        )
        region = (
            self.SRC / "components" / "workspace" / "WorkspaceImageRegion.tsx"
        ).read_text(encoding="utf-8")
        assert 'data-region="image-load-error"' in region, "影像未取回的可读出口不见了"


def test_ratchet_passes():
    """存量 ≤ 基线、且没有新增静默 catch。"""
    result = _run_ratchet()
    assert result.returncode == 0, (
        "静默 catch 棘轮不通过：\n"
        + (result.stdout or "")
        + (result.stderr or "")
    )


def test_ratchet_bites_on_new_silent_catch():
    """反证：新写一个静默 catch，棘轮**必须**判红（4 = 未解析/输入问题也算不通过）。"""
    assert not PROBE.exists(), f"探针文件残留（上一轮未清理？）：{PROBE}"
    PROBE.write_text(
        "export function probe(): number {\n"
        "  try {\n"
        "    return JSON.parse('x').y;\n"
        "  } catch (err) {\n"
        "    return 0;\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    try:
        result = _run_ratchet()
    finally:
        PROBE.unlink(missing_ok=True)
    assert result.returncode != 0, (
        "守卫没有响应新增的静默 catch（假守卫）：\n"
        + (result.stdout or "")
        + (result.stderr or "")
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert "__silent_catch_probe.ts" in combined, f"判红信息里应指出探针文件：{combined[:300]}"
