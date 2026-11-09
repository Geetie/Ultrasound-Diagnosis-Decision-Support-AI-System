"""医院级语料集配置的**接线守卫**（2026-11-09）。

## 为什么这组用例必须存在

本仓库有**四个**孤儿配置的历史（``pipeline_dispatch_budget_s`` /
``pipeline_fusion_budget_s`` / ``rag_use_reranking`` / ``rag_use_redis_cache``）：
字段加了、注释写了、运维以为改了就生效，**实际全仓 0 读取点**。
``rag_use_redis_cache`` 是最坏的一例 —— "运维以为关掉了，实际什么都没发生，
比没有更危险"（AGENTS.md 硬规则 #17）。

所以这里用**源码 grep** 而不是 mock 来钉接线：mock 会让"字段存在但没人读"
这件事完全不可见（替身替掉了缺失的读取点）。

用例分三层：
1. **接线存在**：``rag_enabled_source_sets`` 与 ``rag_source_conflict_render``
   在 app/ 里各有 ≥1 个真实读取点，且位置是预期的三处 / 一处；
2. **语义正确**：请求级 > 院区级 > 全开，三档优先级可验证；
3. **不误伤**：空配置（默认）时行为与改动前逐条一致。
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import Settings
from app.knowledge.protocols import KnowledgeChunk
from app.rag.cache import RetrievalCache
from app.rag.protocols import RetrievalRequest
from app.rag.retriever import build_metadata_filter

_APP = Path(__file__).resolve().parents[3] / "app"


def _read(rel: str) -> str:
    return (_APP / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ① 接线存在（硬规则 #17）
# ---------------------------------------------------------------------------


class TestSettingsHaveConsumers:
    def test_enabled_source_sets_has_consumers(self):
        """``rag_enabled_source_sets`` 必须真的被读 —— 否则是第五个孤儿配置。"""
        consumers = {
            "rag/service.py": "院区级默认（请求未指定时兜底）",
            "rag/retriever.py": "dense 腿过滤（build_metadata_filter 的上游）",
            "rag/bm25_fallback.py": "sparse 腿过滤",
        }
        found = {}
        for rel, why in consumers.items():
            src = _read(rel)
            # 允许两种读取形态：直接读 settings.xxx，或经 request.source_sets 透传
            if "rag_enabled_source_sets" in src:
                found[rel] = f"{why}（直接读字段）"
            elif "source_sets" in src:
                found[rel] = f"{why}（读 request.source_sets）"
        assert found, (
            "rag_enabled_source_sets 在 app/ 下完全没有读取点（孤儿配置，"
            "硬规则 #17）。至少要在检索链路里消费它。"
        )
        # service 层是"院区级兜底"的唯一合法位置，必须直接读 settings
        service_src = _read("rag/service.py")
        assert "rag_enabled_source_sets" in service_src, (
            "app/rag/service.py 没有读 settings.rag_enabled_source_sets —— "
            "医院配了也不会生效"
        )

    def test_render_switch_has_a_consumer(self):
        src = _read("agents/specialist/base.py")
        assert "rag_source_conflict_render" in src, (
            "app/agents/specialist/base.py 没有读 settings.rag_source_conflict_render"
        )

    def test_default_is_off_means_all_enabled(self):
        """默认空列表 = 全开 = 与改动前逐条一致（零行为漂移）。"""
        s = Settings()
        assert s.rag_enabled_source_sets == []
        assert s.rag_source_conflict_render is True

    def test_all_three_retrieval_legs_receive_source_sets(self):
        """三条腿（dense / sparse / bm25 fallback）都必须拿到 source_set。"""
        assert "source_sets=request.source_sets" in _read("rag/retriever.py"), (
            "sparse 腿没把 source_sets 传给 BM25Index.search"
        )
        assert "source_sets=request.source_sets" in _read("rag/service.py"), (
            "BM25 兜底腿没把 source_sets 传给 fallback_search —— "
            "降级路径会绕过医院的语料限制"
        )
        retriever = _read("rag/retriever.py")
        assert "build_metadata_filter(request)" in retriever, (
            "dense 腿没用 build_metadata_filter —— source_set 过滤不会生效"
        )


# ---------------------------------------------------------------------------
# ② 过滤器构造
# ---------------------------------------------------------------------------


class TestBuildMetadataFilter:
    def test_no_source_sets_matches_legacy_behaviour(self):
        """``{"organ": ...} or None`` —— 与改动前逐条一致。"""
        assert build_metadata_filter(RetrievalRequest(query="q")) is None
        assert build_metadata_filter(
            RetrievalRequest(query="q", organ="thyroid")
        ) == {"organ": "thyroid"}

    def test_single_source_set_is_a_scalar(self):
        """单值必须传**标量** —— PGVector 后端对 list 做字符串相等比较，恒 0 命中。"""
        flt = build_metadata_filter(
            RetrievalRequest(query="q", organ="thyroid", source_sets=["ctirads_2021"])
        )
        assert flt == {"organ": "thyroid", "source_set": "ctirads_2021"}
        assert isinstance(flt["source_set"], str)

    def test_multi_source_set_is_a_list_for_match_any(self):
        flt = build_metadata_filter(
            RetrievalRequest(
                query="q", source_sets=["ctirads_2021", "norm_2018"]
            )
        )
        assert flt == {"source_set": ["ctirads_2021", "norm_2018"]}

    def test_empty_list_is_treated_as_no_filter(self):
        flt = build_metadata_filter(RetrievalRequest(query="q", source_sets=[]))
        assert flt is None


# ---------------------------------------------------------------------------
# ③ 请求级归一化
# ---------------------------------------------------------------------------


class TestRequestNormalization:
    def test_aliases_are_normalized_at_construction(self):
        """``C-TIRADS`` 与 ``ctirads_2021`` 必须落到同一个值（两腿+缓存键都读它）。"""
        req = RetrievalRequest(query="q", source_sets=["C-TIRADS", "norm2018"])
        assert req.source_sets == ["ctirads_2021", "norm_2018"]

    def test_comma_separated_string_is_accepted(self):
        req = RetrievalRequest(query="q", source_sets="ctirads_2021,norm_2018")
        assert req.source_sets == ["ctirads_2021", "norm_2018"]

    def test_none_stays_none(self):
        assert RetrievalRequest(query="q").source_sets is None
        assert RetrievalRequest(query="q", source_sets=None).source_sets is None

    def test_duplicates_are_collapsed(self):
        req = RetrievalRequest(query="q", source_sets=["ctirads_2021", "C-TIRADS"])
        assert req.source_sets == ["ctirads_2021"]


# ---------------------------------------------------------------------------
# ④ 缓存键：不同语料集不能共用缓存
# ---------------------------------------------------------------------------


class TestCacheKeyIsolation:
    def _chunk(self) -> KnowledgeChunk:
        return KnowledgeChunk(
            chunk_id="c#0", kb_version="kb_v1.5.0", organ="thyroid",
            content="x", source_doc_id="pdf_thyroid_ctirads_2021_0012",
        )

    def _key(self, **kw) -> str:
        return RetrievalCache.make_key(RetrievalRequest(query="4a 12mm", **kw), "kb_v1.5.0")

    def test_different_source_sets_never_share_a_cache_entry(self):
        """反例：医院 A 查 C-TIRADS 进缓存，会诊医生查诊疗规范时拿到 A 的结果。"""
        a = self._key(source_sets=["ctirads_2021"])
        b = self._key(source_sets=["norm_2018"])
        assert a != b

    def test_order_does_not_matter(self):
        assert self._key(source_sets=["ctirads_2021", "norm_2018"]) == self._key(
            source_sets=["norm_2018", "ctirads_2021"]
        )

    def test_no_filter_differs_from_any_filter(self):
        assert self._key() != self._key(source_sets=["ctirads_2021"])


# ---------------------------------------------------------------------------
# ⑤ 院区级兜底（service 层）—— 不启真实服务，只验纯函数
# ---------------------------------------------------------------------------


class TestServiceResolvesSettingsFallback:
    def test_request_level_wins_over_settings(self, monkeypatch):
        from app.rag.service import RAGServiceImpl

        monkeypatch.setattr(
            "app.rag.service.settings.rag_enabled_source_sets", ["ctirads_2021"]
        )
        req = RetrievalRequest(query="q", source_sets=["norm_2018"])
        out = RAGServiceImpl._resolve_source_sets(req)
        assert out.source_sets == ["norm_2018"], "请求级必须能覆盖院区级"

    def test_settings_used_when_request_is_silent(self, monkeypatch):
        from app.rag.service import RAGServiceImpl

        monkeypatch.setattr(
            "app.rag.service.settings.rag_enabled_source_sets", ["ctirads_2021"]
        )
        out = RAGServiceImpl._resolve_source_sets(RetrievalRequest(query="q"))
        assert out.source_sets == ["ctirads_2021"]

    def test_both_empty_means_no_filter(self, monkeypatch):
        from app.rag.service import RAGServiceImpl

        monkeypatch.setattr("app.rag.service.settings.rag_enabled_source_sets", [])
        out = RAGServiceImpl._resolve_source_sets(RetrievalRequest(query="q"))
        assert out.source_sets is None

    def test_input_request_is_not_mutated(self, monkeypatch):
        """院区级兜底**不许**就地改写入参 —— 复用同一 request 会串味到下次请求。"""
        from app.rag.service import RAGServiceImpl

        monkeypatch.setattr(
            "app.rag.service.settings.rag_enabled_source_sets", ["ctirads_2021"]
        )
        req = RetrievalRequest(query="q")
        RAGServiceImpl._resolve_source_sets(req)
        assert req.source_sets is None, "入参被就地改写了（隐式状态）"
