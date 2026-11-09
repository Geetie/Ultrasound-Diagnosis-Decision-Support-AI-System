"""冲突见证块补捞（2026-11-09）。

## 解决的缺口

阶段3 的并列渲染做出来了，但**在生产默认配置下不触发**：
- `DEFAULT_RAG_TOP_K = 5` + `rag_rerank_output_top_k = 5`
- 而 C-TIRADS「>15 mm 建议 FNA」在 BM25 排**第 32 位**
→ 两套语料的对照条目永远进不了 top-5 → 并列块空 → 能力"做出来了但没生效"。

本模块# 的做法是**定向补捞**（不改排名、不提高 top_k），把对照块单独喂给并列渲染。
"""

from __future__ import annotations

import pytest

from app.rag.witness_backfill import (
    BackfillResult,
    backfill_witness,
    build_witness_queries,
    needs_backfill,
)

pytestmark = pytest.mark.track_rag


def _chunk(cid: str, source_set: str | None = None):
    class _C:
        def __init__(self):
            self.chunk_id = cid
            self.metadata = {"source_set": source_set} if source_set else {}

    class _H:
        def __init__(self):
            self.chunk_id = cid
            self.chunk = _C()

    return _H()


# ---------------------------------------------------------------- needs_backfill
def test_no_chunks_no_backfill():
    need, reason = needs_backfill([])
    assert need is False
    assert reason == "no_chunks"


def test_no_source_set_metadata_no_backfill():
    """没有 source_set 元数据时无从判断多来源 → 不补捞（也不该瞎补）。"""
    need, reason = needs_backfill([_chunk("a"), _chunk("b")])
    assert need is False
    assert reason == "no_source_set_metadata"


def test_single_source_triggers_backfill():
    need, reason = needs_backfill([_chunk("a", "ctirads_2021")])
    assert need is True
    assert reason == "single_source_recalled"


def test_multi_source_no_backfill():
    """已经召回了多个来源 → 交给 source_conflict 判分歧，不需要补。"""
    need, reason = needs_backfill(
        [_chunk("a", "ctirads_2021"), _chunk("b", "norm_2018")]
    )
    assert need is False
    assert reason == "multi_source_already_present"


def test_single_source_enabled_no_backfill():
    """医院只启用一套语料 → 本就不该并列。"""
    need, reason = needs_backfill(
        [_chunk("a", "ctirads_2021")], enabled_source_sets=["ctirads_2021"]
    )
    assert need is False
    assert reason == "single_source_enabled"


# ---------------------------------------------------------------- queries
def test_build_witness_queries_uses_dimensions():
    qs = build_witness_queries("甲状腺 4a 类 12mm 要不要穿刺", {"ctirads_2021"})
    assert qs, "应生成补捞检索式"
    # 检索式是「原查询 + 事实维度」，不含任何硬编码临床数字
    for q in qs:
        assert "甲状腺 4a 类 12mm 要不要穿刺" in q
    joined = " ".join(qs)
    assert "恶性风险" in joined or "FNA" in joined


def test_build_witness_queries_empty_on_empty_query():
    assert build_witness_queries("", set()) == []
    assert build_witness_queries("   ", set()) == []


# ---------------------------------------------------------------- backfill
@pytest.mark.asyncio
async def test_backfill_picks_other_source_only():
    """补捞只收**其他 source_set** 的块，且不重复。"""
    existing = [_chunk("a", "ctirads_2021")]

    async def _retrieve(q: str, k: int):
        return [
            _chunk("a", "ctirads_2021"),   # 已在 → 跳过
            _chunk("b", "norm_2018"),      # 其他来源 → 收
            _chunk("c", "norm_2018"),      # 其他来源 → 收
        ]

    res = await backfill_witness(
        query="甲状腺 4a 类 12mm",
        chunks=existing,
        retrieve_fn=_retrieve,
        max_backfill=2,
    )
    assert res.applied
    ids = [c.chunk_id for c in res.chunks]
    assert "a" not in ids
    assert set(ids) <= {"b", "c"}
    assert len(ids) <= 2


@pytest.mark.asyncio
async def test_backfill_respects_max():
    existing = [_chunk("a", "x")]

    async def _retrieve(q: str, k: int):
        return [_chunk(f"n{i}", "y") for i in range(20)]

    res = await backfill_witness(
        query="q", chunks=existing, retrieve_fn=_retrieve, max_backfill=3
    )
    assert len(res.chunks) == 3


@pytest.mark.asyncio
async def test_backfill_never_raises_on_retrieve_failure():
    """补捞是增强，任何异常都不能打断主流程。"""

    async def _boom(q: str, k: int):
        raise RuntimeError("retriever down")

    res = await backfill_witness(
        query="q", chunks=[_chunk("a", "x")], retrieve_fn=_boom
    )
    assert isinstance(res, BackfillResult)
    assert res.chunks == []


@pytest.mark.asyncio
async def test_backfill_skips_when_not_needed():
    """多来源已在时不做无用检索。"""
    called = {"n": 0}

    async def _retrieve(q: str, k: int):
        called["n"] += 1
        return []

    res = await backfill_witness(
        query="q",
        chunks=[_chunk("a", "x"), _chunk("b", "y")],
        retrieve_fn=_retrieve,
    )
    assert not res.applied
    assert called["n"] == 0, "不需要补捞时不该发起检索"


def test_config_has_read_point():
    """硬规则 #17：新增 settings 字段必须有消费者。"""
    from pathlib import Path

    from app.config.settings import settings

    assert hasattr(settings, "rag_witness_backfill_enabled")
    src = (
        Path(__file__).resolve().parents[3] / "app" / "agents" / "specialist" / "base.py"
    ).read_text(encoding="utf-8")
    assert "rag_witness_backfill_enabled" in src, "配置无读取点（孤儿）"
