"""RRF 两条腿加权（2026-11-08）。

依据：B2 中文检索集（n=117，chunk 级）实测网格
- 纯 dense               hit@10 0.8718
- w_dense:w_bm25 = 1:1   0.9316
- 1:2                    0.9573  ← 取这档（+8.6pt，McNemar p=0.006 显著）
- 1:3                    0.9487
- 2:1                    0.9231

硬规则 #17：新增 settings 字段必须同时有读取点。
本文件既验证默认值，也验证 `_rrf_fuse` 真的按权重算。
"""

from __future__ import annotations

import pytest

from app.config.settings import settings
from app.rag.protocols import RetrievalMethod

pytestmark = pytest.mark.track_rag


def test_default_weights_match_measured_best():
    """默认权重必须是实测最优的 1:2，不能是等权。"""
    assert settings.rag_rrf_w_dense == 1.0
    assert settings.rag_rrf_w_bm25 == 2.0


def test_weights_have_a_read_point():
    """硬规则 #17：字段必须有消费者，否则是孤儿配置。

    用源码 grep 而不是 mock —— 孤儿配置的典型特征就是「设了但没人读」，
    mock 掉依赖反而看不出这一点。
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[3] / "app" / "rag" / "retriever.py").read_text(
        encoding="utf-8"
    )
    assert "rag_rrf_w_dense" in src, "rag_rrf_w_dense 无读取点（孤儿配置）"
    assert "rag_rrf_w_bm25" in src, "rag_rrf_w_bm25 无读取点（孤儿配置）"


def test_sparse_weight_actually_affects_fusion():
    """行为验证：只出现在 sparse 腿的 chunk，其融合分应被 w_bm25 放大。

    构造两条腿各一个不同 chunk，sparse 权重 2.0 → sparse 那个 chunk 的分是 dense 的 2 倍。
    """
    from app.rag.retriever import Retriever

    r = Retriever.__new__(Retriever)  # 只测纯函数，不走 __init__ 的重依赖
    r._rrf_k = settings.rag_rrf_k

    class _H:
        def __init__(self, cid, score):
            self.chunk_id = cid
            self.chunk = {"chunk_id": cid}
            self.score = score
            self.source = None
            self.rerank_score = None
            self.retrieval_source = None

    legs = {
        RetrievalMethod.DENSE: [_H("only_dense", 0.9)],
        RetrievalMethod.SPARSE: [_H("only_sparse", 0.9)],
    }
    fused = r._rrf_fuse(legs)
    by_id = {h.chunk_id: h.score for h in fused}
    # 同一 rank（都是第 1 名），分差 = 权重比
    assert by_id["only_sparse"] > by_id["only_dense"]
    ratio = by_id["only_sparse"] / by_id["only_dense"]
    assert abs(ratio - 2.0) < 1e-6, f"权重比应为 2.0，实测 {ratio}"
