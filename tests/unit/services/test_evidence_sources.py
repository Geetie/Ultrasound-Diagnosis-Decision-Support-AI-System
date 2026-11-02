"""A.126 / G23：证据溯源 —— chunk_id → 来源文档 / 章节。

缺口：结果契约只有裸 `chunk_id`，工作区与报告只能显示 ID，界面自己写着"暂未提供点击跳转"。
`knowledge_chunks` 表本来就有 `source_doc_id` / `section_path`，只是读路径没取。

本组守卫：①批量查询只取白名单两列并投影成 map；②查不到的 chunk 不编造；
③DB 异常 → 空 dict + warning（不能把结果接口拖成 500）；④空/重复 id 的处理。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import evidence_sources as es


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: self.rows))


@pytest.mark.asyncio
async def test_loads_and_projects_whitelist():
    db = _DB(
        [
            ("kb_v1.6.0:d1#0001", "acr_ti_rads_2017", "第4章/4.2 结节特征"),
            ("kb_v1.6.0:d1#0002", "acr_ti_rads_2017", ""),
        ]
    )
    out = await es.load_evidence_sources(db, ["kb_v1.6.0:d1#0001", "kb_v1.6.0:d1#0002"])
    assert out["kb_v1.6.0:d1#0001"] == {
        "source_doc_id": "acr_ti_rads_2017",
        "section_path": "第4章/4.2 结节特征",
    }
    # 空 section_path 不写键（不造假字段）
    assert out["kb_v1.6.0:d1#0002"] == {"source_doc_id": "acr_ti_rads_2017"}


@pytest.mark.asyncio
async def test_missing_chunk_is_not_invented():
    db = _DB([("a", "doc", "sec")])
    out = await es.load_evidence_sources(db, ["a", "b-missing"])
    assert set(out) == {"a"}


@pytest.mark.asyncio
async def test_empty_input_skips_query():
    db = _DB([])
    assert await es.load_evidence_sources(db, []) == {}
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_db_failure_returns_empty_and_warns(monkeypatch):
    db = _DB([])
    db.execute = AsyncMock(side_effect=RuntimeError("db down"))
    warnings: list[str] = []

    class _Logger:
        def warning(self, event, **kw):
            warnings.append(event)

    monkeypatch.setattr(es, "logger", _Logger())
    assert await es.load_evidence_sources(db, ["x"]) == {}
    assert "evidence_sources_query_failed" in warnings


@pytest.mark.asyncio
async def test_endpoint_attaches_sources(monkeypatch):
    """结果接口把来源挂到 evidence.sources；查不到 → None（不造空字典）。"""
    from app.api.v1 import diagnosis as api

    async def fake_load(db, ids):
        return {"c1": {"source_doc_id": "doc1", "section_path": "§1"}}

    monkeypatch.setattr(es, "load_evidence_sources", fake_load)
    result = SimpleNamespace(knowledge_ids=["c1", "c2"])
    out = await api._evidence_sources_for(object(), result)
    assert out == {"c1": {"source_doc_id": "doc1", "section_path": "§1"}}


@pytest.mark.asyncio
async def test_endpoint_without_ids_returns_none():
    from app.api.v1 import diagnosis as api

    assert await api._evidence_sources_for(object(), SimpleNamespace(knowledge_ids=None)) is None
    assert await api._evidence_sources_for(object(), SimpleNamespace(knowledge_ids=[])) is None
