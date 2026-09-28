"""文档"信息守恒"守卫。

## 为什么有这条测试

2026-09-28 的文档重写事故：我用"逐条对码核验"作为重写依据，而该方法**只能覆盖有代码对应物
的内容**。旧文档里大量内容是**领域规格**（分级标准、阈值口径、需求分析、环境规格、验收标准），
它们在代码里没有对应物，于是被连带压缩掉 —— 11 份文档从 5928 行压到 2032 行（-66%），
其中 `06` 丢 116 个标题、`13` 丢 85 个、`01` 丢 29 个。

**教训**：核验能力 ≠ 删除授权。文档里有两类内容必须分开处理：

- ① **事实陈述**（描述系统现状）—— 代码可以证伪，允许改写表述；
- ② **规范/需求/决策**（定义系统该是什么样）—— **代码无权证伪**，只能保留 + 加标注。

这条测试把方法论变成机器约束：**基线里出现过的标题，不许消失**。
要"废弃"某个标题，必须在 `RETIRED_HEADINGS` 里登记**理由**——把静默删除变成显式决策。

## 基线来源

`tests/fixtures/doc_headings_baseline.json`（由重写前的 `ac87400` 生成快照）。
不在测试里跑 `git show`：CI 默认浅克隆（depth=1），历史不可用。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

DOCS_DIR = (
    Path(__file__).resolve().parents[3]
    / "docs-for-guiding generating docs"
    / "超声诊疗决策支持系统"
)
BASELINE_FILE = Path(__file__).resolve().parents[2] / "fixtures" / "doc_headings_baseline.json"

# 允许"确实应当废弃"的标题：键=文档名，值={标题: 理由}
# 规则：**必须写明理由**，且理由不得是"核验不了""已重写"这类无信息量的说法。
# 当前为空 —— 说明至今没有任何标题被授权删除。
RETIRED_HEADINGS: dict[str, dict[str, str]] = {}


def _load_baseline() -> dict:
    assert BASELINE_FILE.exists(), (
        f"缺少标题基线快照 {BASELINE_FILE.as_posix()}；"
        "它由 .git/_gen_doc_baseline.py 从重写前的提交生成"
    )
    return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))


BASELINE = _load_baseline()


def _current_headings(path: Path) -> set[str]:
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("#")}


@pytest.mark.parametrize("doc_name", sorted(BASELINE))
def test_no_baseline_heading_disappears(doc_name: str) -> None:
    """基线标题必须仍然存在 —— 这是"信息守恒"的核心断言。"""
    entry = BASELINE[doc_name]
    path = DOCS_DIR / doc_name
    assert path.exists(), f"文档被删除或改名：{doc_name}"

    current = _current_headings(path)
    retired = RETIRED_HEADINGS.get(doc_name, {})

    missing = [
        heading
        for heading in entry["baseline_headings"]
        if heading not in current and heading not in retired
    ]

    assert missing == [], (
        f"《{doc_name}》丢失了 {len(missing)} 个章节标题（基线来源 {entry['source']}）：\n"
        + "\n".join(f"  - {m}" for m in missing[:20])
        + "\n如确应废弃，请在 RETIRED_HEADINGS 中登记**理由**，不要静默删除。"
    )


@pytest.mark.parametrize("doc_name", sorted(BASELINE))
def test_document_did_not_shrink(doc_name: str) -> None:
    """行数不得低于基线的 90%（防止"重写"变成"压缩"）。"""
    entry = BASELINE[doc_name]
    baseline_lines = entry["baseline_line_count"]
    if baseline_lines < 20:  # 极短文档不设阈值
        pytest.skip("文档过短，不设行数阈值")

    current_lines = len((DOCS_DIR / doc_name).read_text(encoding="utf-8").splitlines())
    ratio = current_lines / baseline_lines

    assert ratio >= 0.9, (
        f"《{doc_name}》行数显著下降：{baseline_lines} -> {current_lines}"
        f"（{ratio:.0%}）。重写的目标是「补现状 + 纠错误」，不是压缩篇幅。"
    )


def test_retired_headings_have_reasons() -> None:
    """废弃登记必须带实质理由 —— 防止把"删除"洗成"登记"。"""
    empty_reason = [
        f"{doc}: {heading!r}"
        for doc, items in RETIRED_HEADINGS.items()
        for heading, reason in items.items()
        if len(reason.strip()) < 10
    ]

    assert empty_reason == [], f"以下废弃登记缺少实质理由：{empty_reason}"


def test_restored_docs_declare_their_merge_rule() -> None:
    """已恢复的文档必须在头部写清合并规则（原文保留 + 实证层），供读者理解结构。"""
    restored = [name for name in BASELINE if "01-系统架构" in name or "13-医生" in name]

    for name in restored:
        text = (DOCS_DIR / name).read_text(encoding="utf-8")
        assert "合并说明" in text, f"《{name}》缺少头部的合并说明"
        assert "附录 A" in text, f"《{name}》缺少代码实证层（附录 A）"
