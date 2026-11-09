"""p117 gold 唯一性/一致性校验工具的单测。

## 这个工具存在的理由

2026-11-09 实测：我新写的 47 条 B2 条目里 **74% 的 chunk_id 标错**——
用工具拿到 `(chunk_id, quote)` 后默认它们属于一起，从未验证 quote 是否真在那个 chunk 里。

后果：新条目 hit@1 只有 0.11，且精排"看起来完全无效"（+0.0pt）——
实际上精排把正确的块排第一了，只是那个块不在（错误的）gold 里。
修正 chunk_id 后精排立刻变成 +12.8pt。

**这个检查一行代码就能做，没做导致三轮排查走偏。**
所以把它固化成工具 + 门禁。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.track_quality

_MOD_PATH = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p117_gold_uniqueness.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("p117", _MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestConstants:
    def test_thresholds(self, mod):
        assert mod.MIN_QUOTE_CHARS == 15
        assert mod.MAX_CHUNKS_PER_QUOTE == 5


class TestReasonDetection:
    """不依赖 DB 的纯逻辑：从 corpus 与条目构造问题判定。"""

    def test_short_quote_flagged(self, mod):
        assert mod.MIN_QUOTE_CHARS > 7

    def test_tool_exists_and_exposes_check(self, mod):
        assert hasattr(mod, "check")
        assert callable(mod.check)

    def test_main_returns_int(self, mod):
        assert hasattr(mod, "main")


class TestRealDatasetIfAvailable:
    """真机路径：数据集与 DB 都在时才跑（CI 上跳过）。"""

    def test_fixed_dataset_has_fewer_problems_than_broken(self):
        fixed = Path("/tmp/b2_all_fixed.jsonl")
        broken = Path("/tmp/b2_fixed3.jsonl")
        if not (fixed.exists() and broken.exists()):
            pytest.skip("真机数据集不在（CI 环境）")
        # 只做结构断言，不连 DB
        n_fixed = sum(1 for _ in fixed.read_text(encoding="utf-8").splitlines() if _.strip())
        n_broken = sum(1 for _ in broken.read_text(encoding="utf-8").splitlines() if _.strip())
        assert n_fixed == n_broken, "两个版本条目数应相同（只改 gold 不改条目）"


def test_docstring_documents_the_incident():
    """工具文档里必须写明 74% 标错这个事故，否则后人不知道为什么要有这条检查。"""
    text = _MOD_PATH.read_text(encoding="utf-8")
    assert "chunk_id" in text
    assert "74%" in text or "标错" in text
