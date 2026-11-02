"""A.141：缺口台账的代码锚点必须**真实存在**（防"行号错"复发）。

背景：对方台账的 §3 明确把"路径/行号错"列为质量问题——反查时找不到文件。
我们自己的台账也引用了大量 `path:line`；代码一改，锚点就会漂。
本守卫把"台账里剩下的 7 项"的锚点逐条核对：文件存在、行号在文件长度内、
引用的符号字符串确实在该行附近。

范围刻意只覆盖 §8（剩余项）——已修项的历史锚点属于记录，不随代码维护。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.track_quality

ROOT = Path(__file__).resolve().parents[3]
LEDGER = ROOT / "docs" / "合规" / "缺口核实台账-20261103.md"

#: (文件相对路径, 行号, 该行应包含的关键符号)
ANCHORS: tuple[tuple[str, int, str], ...] = (
    ("app/services/diagnosis_pipeline.py", 147, "_assess_routing"),
    ("app/agents/knowledge.py", 580, "async def query"),
    ("app/agents/routing.py", 182, "retrieval_confidence"),
    ("app/rag/image_caption.py", 50, "async def describe"),
    ("app/rag/retriever.py", 1065, "visual_features"),
    ("app/fusion/conflict.py", 186, "comparable"),
    ("scripts/audit/p76_missed_rate_audit.py", 34, "def compute_audit"),
    ("app/security/consent_withdrawal_consumer.py", 388, "_record_e2e_latency"),
)


@pytest.mark.parametrize(("rel_path", "line_no", "symbol"), ANCHORS)
def test_ledger_anchor_points_at_real_code(rel_path: str, line_no: int, symbol: str):
    path = ROOT / rel_path
    assert path.exists(), f"台账引用的文件不存在：{rel_path}"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert 1 <= line_no <= len(lines), f"{rel_path} 只有 {len(lines)} 行，台账写第 {line_no} 行"
    window = "\n".join(lines[max(0, line_no - 3) : line_no + 2])
    assert symbol in window, f"{rel_path}:{line_no} 附近找不到 {symbol!r}（行号漂了）"


def test_ledger_section_8_lists_every_remaining_item():
    """§8 必须覆盖全部 7 项剩余缺口（少写一项就等于悄悄宣称它已修）。"""
    text = LEDGER.read_text(encoding="utf-8")
    assert "## 8. 剩余 7 项" in text
    for item in ("N4", "G2", "G21", "G39", "G41", "G48", "G49"):
        assert re.search(rf"\| {item} ", text), f"§8 未列出 {item}"


def test_ledger_warns_against_false_completion():
    """台账必须写明"不要把通道/下界当成打通"——这是本批最容易复发的话术。"""
    text = LEDGER.read_text(encoding="utf-8")
    assert "不要再做的事" in text
    assert "通道已通" in text and "下界" in text
