"""图查询递归降级实现（二期 §12）。

不变量：
1. **必须有环检测**：知识图谱天然有环，递归 CTE 少了 `NOT target = ANY(path)` 会一路
   展开到 max_depth，深度一大就是指数级爆炸；
2. **脏数据不能炸查询**：metadata.relations 不是数组时 jsonb_array_elements 会直接报错，
   整个查询 500；必须有类型判断；
3. 单条语句完成遍历（不做逐层往返）；
4. 图查询失败一律降级为空结果——它不在诊断关键路径上。
"""
from types import SimpleNamespace

import pytest

from app.config.settings import settings
from app.knowledge.graph_query import PgVectorGraphAdapter


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _CapturingSession:
    def __init__(self, rows=()):
        self.statements: list = []
        self._rows = list(rows)

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params))
        return _FakeResult(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _chunk_row(chunk_id="c1"):
    return SimpleNamespace(
        _mapping={
            "chunk_id": chunk_id,
            "kb_version": "kb_v1.0.0",
            "organ": "thyroid",
            "content": "结节内容",
            "metadata": {"relations": []},
            "source_doc_id": "doc_1",
            "section_path": "TI-RADS/4",
            "token_count": 12,
        }
    )


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "graph_query_enabled", True)
    yield


@pytest.fixture
def adapter_factory():
    def _make(rows=()):
        session = _CapturingSession(rows)
        adapter = PgVectorGraphAdapter(session_factory=lambda: session)
        return adapter, session

    return _make


class TestDisabledByDefault:
    async def test_neighbors_disabled(self, adapter_factory, monkeypatch):
        monkeypatch.setattr(settings, "graph_query_enabled", False)
        adapter, session = adapter_factory()

        assert await adapter.query_neighbors("c1") == []
        assert session.statements == [], "关闭时不应触碰数据库"

    async def test_path_disabled(self, adapter_factory, monkeypatch):
        monkeypatch.setattr(settings, "graph_query_enabled", False)
        adapter, session = adapter_factory()

        assert await adapter.traverse_path("c1", "c2") == []
        assert session.statements == []

    async def test_contradictions_disabled(self, adapter_factory, monkeypatch):
        monkeypatch.setattr(settings, "graph_query_enabled", False)
        adapter, session = adapter_factory()

        assert await adapter.check_contradictions(["c1", "c2"]) == []
        assert session.statements == []


class TestNeighbors:
    async def test_single_recursive_statement(self, adapter_factory, enabled):
        adapter, session = adapter_factory(rows=[_chunk_row()])

        await adapter.query_neighbors("c1", max_depth=3)

        assert len(session.statements) == 1, "逐层往返会产生 N 次查询"
        sql = session.statements[0][0]
        assert "WITH RECURSIVE" in sql
        assert sql.count("SELECT") >= 2

    async def test_cycle_guard_present(self, adapter_factory, enabled):
        """缺环检测时深层图遍历会爆炸。"""
        adapter, session = adapter_factory()

        await adapter.query_neighbors("c1", max_depth=5)

        sql = session.statements[0][0]
        assert "ANY(w.path)" in sql
        assert "path ||" in sql

    async def test_dirty_metadata_guard_present(self, adapter_factory, enabled):
        """relations 不是数组时必须当空处理，而不是让查询报错。"""
        adapter, session = adapter_factory()

        await adapter.query_neighbors("c1")

        sql = session.statements[0][0]
        assert "jsonb_typeof" in sql
        assert "'[]'::jsonb" in sql

    async def test_params_bound(self, adapter_factory, enabled):
        adapter, session = adapter_factory()

        await adapter.query_neighbors("c1", relation_type="supports", max_depth=2)

        params = session.statements[0][1]
        assert params["chunk_id"] == "c1"
        assert params["relation_type"] == "supports"
        assert params["max_depth"] == 2

    async def test_depth_floored_to_one(self, adapter_factory, enabled):
        adapter, session = adapter_factory()

        await adapter.query_neighbors("c1", max_depth=0)

        assert session.statements[0][1]["max_depth"] == 1

    async def test_empty_chunk_id_short_circuits(self, adapter_factory, enabled):
        adapter, session = adapter_factory()

        assert await adapter.query_neighbors("") == []
        assert session.statements == []

    async def test_rows_mapped_to_chunks(self, adapter_factory, enabled):
        adapter, _ = adapter_factory(rows=[_chunk_row("c9")])

        chunks = await adapter.query_neighbors("c1")

        assert len(chunks) == 1
        assert chunks[0].chunk_id == "c9"
        assert chunks[0].organ == "thyroid"
        assert chunks[0].token_count == 12

    async def test_unmappable_row_is_dropped(self, adapter_factory, enabled):
        """无法映射的行直接丢弃：造"空块"会在下游变成无内容无来源的幽灵候选。"""
        adapter, _ = adapter_factory(rows=[SimpleNamespace(), _chunk_row("c2")])

        chunks = await adapter.query_neighbors("c1")

        assert [chunk.chunk_id for chunk in chunks] == ["c2"]

    async def test_dirty_metadata_is_replaced_for_chunk(self, adapter_factory, enabled):
        row = _chunk_row()
        row._mapping["metadata"] = "not-a-dict"
        adapter, _ = adapter_factory(rows=[row])

        chunks = await adapter.query_neighbors("c1")

        assert chunks[0].metadata == {}


class TestTraversePath:
    async def test_orders_by_depth_and_dedups(self, adapter_factory, enabled):
        adapter, session = adapter_factory(rows=[_chunk_row()])

        await adapter.traverse_path("c1", "c9", max_hops=4)

        sql = session.statements[0][0]
        assert "DISTINCT ON (kc.chunk_id)" in sql
        assert "ORDER BY kc.chunk_id, w.depth" in sql
        assert session.statements[0][1]["max_hops"] == 4

    async def test_missing_endpoints_short_circuit(self, adapter_factory, enabled):
        adapter, session = adapter_factory()

        assert await adapter.traverse_path("c1", "") == []
        assert session.statements == []

    async def test_target_filter_applied(self, adapter_factory, enabled):
        adapter, session = adapter_factory()

        await adapter.traverse_path("c1", "c9")

        sql = session.statements[0][0]
        assert "w.target_id = :to_chunk_id" in sql


class TestContradictions:
    async def test_single_statement_with_opposing_relations(self, adapter_factory, enabled):
        rows = [
            SimpleNamespace(
                _mapping={"target_id": "t1", "source_a": "a", "source_b": "b"}
            )
        ]
        adapter, session = adapter_factory(rows=rows)

        result = await adapter.check_contradictions(["a", "b"])

        assert result == [("t1", "a", "b")]
        assert len(session.statements) == 1
        params = session.statements[0][1]
        assert params["positive"] == "supports"
        assert params["negative"] == "excludes"
        assert params["chunk_ids"] == ["a", "b"]

    async def test_requires_at_least_two_chunks(self, adapter_factory, enabled):
        adapter, session = adapter_factory()

        assert await adapter.check_contradictions(["only"]) == []
        assert session.statements == []

    async def test_blank_ids_filtered(self, adapter_factory, enabled):
        adapter, session = adapter_factory()

        await adapter.check_contradictions(["a", "", "b"])

        assert session.statements[0][1]["chunk_ids"] == ["a", "b"]


class TestFailureIsolation:
    async def test_query_failure_returns_empty(self, enabled, monkeypatch):
        class _BrokenSession:
            async def execute(self, statement, params=None):
                raise RuntimeError("db down")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

        adapter = PgVectorGraphAdapter(session_factory=lambda: _BrokenSession())

        assert await adapter.query_neighbors("c1") == []
        assert await adapter.traverse_path("c1", "c2") == []
        assert await adapter.check_contradictions(["a", "b"]) == []

    async def test_plain_session_object_supported(self, enabled):
        """注入的工厂直接返回会话（而非上下文管理器）时也要能跑。"""
        session = _CapturingSession(rows=[_chunk_row()])
        adapter = PgVectorGraphAdapter(session_factory=lambda: session)

        chunks = await adapter.query_neighbors("c1")

        assert chunks[0].chunk_id == "c1"
