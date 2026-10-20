"""A.70.3 守卫：专科提示词**不得**含可被整段照抄的占位符。

真机证据（A.70.1/A.70.3）：`cardiac.md` 的示例写着 `证据: chunk_a1b2, chunk_c3d4`，
模型原样照抄该行（复现率：cardiac 必现、thyroid/liver 该次未复现）。
把示例里的假 ID 换成编号 `[1], [2]` 后，同一模型同一条查询输出 `证据: [1], [2]` —— **不再照抄**。

本守卫钉住"提示词里不许再出现这些占位符"，防止以后有人为了"示例更像真的"又把它们加回来。
"""

from __future__ import annotations

import re
from pathlib import Path

_PROMPTS = Path(__file__).resolve().parents[3] / "app" / "agents" / "prompts"

#: 曾经的占位符（真实证据 ID 形如 `kb_v1.3.0:pdf_..._0003#0000`，不会长这样）
_FORBIDDEN = (
    (re.compile(r"chunk_[a-z0-9]{4}\b"), "假 chunk ID（如 chunk_a1b2）"),
    (re.compile(r"PATIENT_\d+"), "假病人 ID（如 PATIENT_001）"),
)


def test_specialist_prompts_have_no_copyable_placeholders():
    checked = 0
    problems: list[str] = []
    for path in sorted(_PROMPTS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        checked += 1
        for pattern, label in _FORBIDDEN:
            if pattern.search(text):
                problems.append(f"{path.name}: 含{label}")
    assert checked >= 3, "至少要检查 thyroid/cardiac/liver 三份模板"
    assert problems == [], "提示词里出现了可被整段照抄的占位符：" + "；".join(problems)


def test_output_example_heading_is_not_the_copyable_marker():
    """示例标题用 `# 输出示例`，不是模型会原样吐出的 `# 输出`。"""
    for name in ("thyroid.md", "cardiac.md", "liver.md"):
        text = (_PROMPTS / name).read_text(encoding="utf-8")
        assert "# 输出示例" in text, f"{name} 缺少示例标题"
        # 允许 `# 输出示例`，但不允许单独一行 `# 输出`
        assert not re.search(r"^#\s*输出\s*$", text, re.MULTILINE), f"{name} 仍有裸 `# 输出` 标题"
