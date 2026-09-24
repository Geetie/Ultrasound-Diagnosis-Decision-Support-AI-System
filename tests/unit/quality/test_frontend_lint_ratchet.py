"""前端 lint 收敛棘轮（quality-debt）。

现状：0 error / 26 warning（几乎全是 ``@typescript-eslint/no-explicit-any``）。
warning 默认不阻断，所以"下次再多几十条"没人会拦。这里用 ``--max-warnings`` 把当前
水位钉住：新增任何一条告警即 CI 红，同时水位只允许下调。

为什么不一次清完 26 条
----------------------
这些 ``any`` 分布在 dicomStore、DiagnosisConclusion 等既有模块，逐条替换需要判断真实
类型（不少是第三方库未导出类型的边界），改错了会把编译期错误引进来。按棘轮先止损、
再逐轮收敛，比一次性"为了清零而 any→unknown 到处断言"更安全。
"""
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_JSON = REPO_ROOT / "frontend" / "package.json"

# 当前水位：只允许下调（下调后请同步改这个常量）
MAX_WARNINGS = 26


@pytest.fixture(scope="module")
def scripts() -> dict:
    if not PACKAGE_JSON.exists():
        pytest.skip("未找到 frontend/package.json")
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8")).get("scripts", {})


class TestFrontendLintRatchet:
    def test_lint_is_blocking_on_new_warnings(self, scripts):
        """lint 必须带 --max-warnings，否则新增告警悄悄通过、收敛无从谈起。"""
        lint = scripts.get("lint", "")

        assert "--max-warnings" in lint, (
            "npm run lint 未设置 --max-warnings：新增告警不会阻断"
        )

    def test_threshold_matches_recorded_watermark(self, scripts):
        """水位与常量必须一致——只改 package.json 会让棘轮失真。"""
        lint = scripts.get("lint", "")
        match = re.search(r"--max-warnings[= ](\d+)", lint)

        assert match is not None, "无法从 lint 脚本解析 --max-warnings 数值"
        assert int(match.group(1)) == MAX_WARNINGS, (
            f"lint 水位 {match.group(1)} 与棘轮常量 {MAX_WARNINGS} 不一致；"
            "下调时请同时更新本文件"
        )

    def test_report_script_has_no_threshold(self, scripts):
        """报告脚本要保持不阻断，便于查看完整存量。"""
        report = scripts.get("lint:report")

        assert report is not None, "缺少 lint:report，收敛过程会失去存量视图"
        assert "--max-warnings" not in report

    def test_threshold_is_not_trivially_large(self):
        """防止把水位设成一个"等于没开"的数字。"""
        assert MAX_WARNINGS < 100
