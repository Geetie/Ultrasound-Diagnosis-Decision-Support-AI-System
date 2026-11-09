"""精排条件化（2026-11-09）。

## 为什么精排不该"全局开或全局关"

`Drowning in Documents`（arXiv 2411.11767）实测：
> "reranking a small set of documents, e.g. **less than 100**, is an effective way to
>  improve recall... as we increase k further **beyond 100**, the rerankers' Recall@10
>  **dramatically decreases**"

本项目 B2（n=117）实测：depth 20 与 40 **四项指标逐位相同** → 取 20 省一半延迟。

## 另外两个必须挡住的情形

1. **候选太少**：交叉编码器没有区分度可做（全是同一段的近似副本）。
2. **精排模型选错**：`bge-reranker-base` 在 3 个基座 × 4 种深度上全部为负
   （NDCG 95%CI 全程为负 [-0.1445, -0.0151]）。必须用 v2-m3。

本文件钉住「条件」与「模型」两件事，防止有人把它们改回去。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config.settings import settings

pytestmark = pytest.mark.track_rag

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_reranker_model_is_not_the_negative_one():
    """踩过的坑：bge-reranker-base 在 B2 上是真负增益，不能回退。"""
    assert settings.bge_reranker_model != "bge-reranker-base", (
        "bge-reranker-base 在 B2（n=117）上 3 个基座 × 4 种深度全部为负，"
        "NDCG 95%CI 全程为负（[-0.1445, -0.0151]）—— 真负增益，不是噪声。"
        "换 bge-reranker-v2-m3 后 hit@1 +12.0pt (p=0.0201)。"
    )


def test_gating_settings_exist_with_sane_defaults():
    assert settings.rag_rerank_min_candidates >= 1
    # 硬上限必须在文献拐点（100）以内，且不能大过召回深度太多
    assert 1 <= settings.rag_rerank_max_depth <= 100, (
        f"rag_rerank_max_depth={settings.rag_rerank_max_depth} 超出 arXiv 2411.11767 "
        "的实测拐点（>100 时 Recall@10 急剧下降）"
    )


def test_gating_settings_have_read_points():
    """硬规则 #17：字段必须有消费者。"""
    src = (_REPO_ROOT / "app" / "rag" / "service.py").read_text(encoding="utf-8")
    assert "rag_rerank_min_candidates" in src, "rag_rerank_min_candidates 无读取点"
    assert "rag_rerank_max_depth" in src, "rag_rerank_max_depth 无读取点"


def test_skip_is_logged_not_silent():
    """跳过精排必须留痕（AGENTS.md「零静默失败」）。"""
    src = (_REPO_ROOT / "app" / "rag" / "service.py").read_text(encoding="utf-8")
    assert '"rerank_skipped"' in src, "跳过精排没有日志 → 无法区分「没开」和「开了但跳过了」"
    assert "too_few_candidates" in src


class TestRerankGate:
    """`rerank_gate` 纯函数的行为验证（六个分支全覆盖）。"""

    def _gate(self, n, **kw):
        from app.rag.service import rerank_gate

        defaults = dict(use_reranking=True, min_candidates=10, max_depth=20)
        defaults.update(kw)
        return rerank_gate(n, **defaults)

    def test_normal_case_truncates_to_max_depth(self):
        """候选 40 → 精排，深度截到 20（实测 @20 与 @40 指标逐位相同，取小值省延迟）。"""
        assert self._gate(40) == (True, "", 20)

    def test_exactly_max_depth_not_truncated(self):
        assert self._gate(20) == (True, "", 20)

    def test_below_min_candidates_skips(self):
        """候选不足 → 跳过（交叉编码器在近似副本上没有区分度可做）。"""
        assert self._gate(9) == (False, "too_few_candidates", 0)

    def test_at_min_candidates_does_not_skip(self):
        assert self._gate(10) == (True, "", 10)

    def test_zero_results_skips_with_own_reason(self):
        """零命中留自己的原因（不聚合成 too_few_candidates，AGENTS.md 硬规则 8）。"""
        assert self._gate(0) == (False, "no_results", 0)

    def test_disabled_by_request(self):
        assert self._gate(40, use_reranking=False) == (False, "disabled", 0)

    def test_zero_means_no_limit(self):
        assert self._gate(200, max_depth=0) == (True, "", 200)
        assert self._gate(5, min_candidates=0) == (True, "", 5)
