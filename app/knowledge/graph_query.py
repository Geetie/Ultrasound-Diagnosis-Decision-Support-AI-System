"""GraphQueryAdapter Protocol — 知识图谱查询抽象层。

P2-KB-08 STATUS: 一期 STUB（接入条件明确化）

一期现状 (2026-06-30):
- 定义 GraphQueryAdapter Protocol 接口
- 提供默认实现 PgVectorGraphAdapter，但三个方法 (query_neighbors /
  traverse_path / check_contradictions) 均返回空列表
- 空调用会记录 WARNING 日志，便于生产环境捕获误调用

接入条件 (满足任一即实现完整功能):
  1. settings.graph_query_enabled = True  ← 在 app/config/settings.py 中
     暴露的开关（默认 False）
  2. 二期 Neo4j 部署完成 → 用 Cypher 实现路径遍历
  3. PGVector 递归 CTE 实现就绪（alembic migration 0010+）
     → 用递归 CTE 实现 query_neighbors + check_contradictions

设计规格:
- 一期仅定义接口 + 占位实现，业务层通过 GraphQueryAdapter Protocol 抽象
  不受影响
- 二期切换 Neo4j Cypher 或 PGVector 递归 CTE 都不需要改业务代码

调用方应在注入时检查 settings.graph_query_enabled：
    if settings.graph_query_enabled:
        contradictions = await graph_adapter.check_contradictions(chunk_ids)
    # else: 业务层应假设 graph_query 不可用，自行 fallback
"""
from __future__ import annotations

from typing import Any, List, Optional, Protocol

import structlog

from app.knowledge.protocols import KnowledgeChunk

logger = structlog.get_logger("app.knowledge.graph_query")


class GraphQueryAdapter(Protocol):
    """知识图谱查询适配器协议。

    Methods:
        query_neighbors: 查询与指定 chunk 相邻的节点。
        traverse_path: 沿关系路径遍历两个 chunk 之间的路径。
        check_contradictions: 检查给定 chunks 之间是否存在矛盾关系。
    """

    async def query_neighbors(
        self,
        chunk_id: str,
        relation_type: Optional[str] = None,
        max_depth: int = 1,
    ) -> List[KnowledgeChunk]:
        """查询与指定 chunk 相邻的节点。

        Args:
            chunk_id: 起始 chunk 的 ID。
            relation_type: 可选的关系类型过滤。
            max_depth: 最大遍历深度（默认 1）。

        Returns:
            相邻节点的 KnowledgeChunk 列表。
        """
        ...

    async def traverse_path(
        self,
        from_chunk_id: str,
        to_chunk_id: str,
        max_hops: int = 5,
    ) -> List[KnowledgeChunk]:
        """沿关系路径遍历两个 chunk 之间的路径。

        Args:
            from_chunk_id: 起始 chunk ID。
            to_chunk_id: 目标 chunk ID。
            max_hops: 最大跳数（默认 5）。

        Returns:
            路径上的 KnowledgeChunk 列表（含起止节点）。
        """
        ...

    async def check_contradictions(
        self,
        chunk_ids: List[str],
    ) -> List[tuple[str, str, str]]:
        """检查给定 chunks 之间是否存在矛盾关系。

        Args:
            chunk_ids: 待检查的 chunk ID 列表。

        Returns:
            矛盾三元组列表: [(chunk_id_a, chunk_id_b, contradiction_type), ...]
        """
        ...


class PgVectorGraphAdapter:
    """PGVector + 递归 CTE 实现（二期 §12，无需 Neo4j/AGE）。

    边从哪来
    --------
    不新增图数据库，也不新增边表：边的投影定义为 ``knowledge_chunks.metadata`` 里的
    ``relations`` 数组（``[{"target": "<chunk_id>", "relation": "differential_of"}]``）。
    这是 KB 构建侧需要遵守的契约——本轮交付可运行的查询实现，边数据由知识库构建填充；
    没有该字段时查询返回空表（不是报错），因此可以先上线查询能力、后补边数据。

    两个护栏
    --------
    1. **环检测**：知识图谱天然有环（A 是 B 的鉴别、B 又是 A 的鉴别）。递归 CTE 不加
       ``NOT target = ANY(path)`` 会一直展开到 max_depth，深度稍大就是指数级爆炸。这里
       维护已访问路径并在每次递归时排除。
    2. **脏数据不炸**：``metadata->'relations'`` 若不是数组，``jsonb_array_elements``
       会直接报错让整个查询 500。用 ``jsonb_typeof`` 判类型后再展开，非数组视为无边。

    失败一律降级为空结果：图查询是检索增强，不在关键路径上，不该因为图查不到就让
    诊断失败。
    """

    # 边投影：把 metadata.relations 展开成 (source, target, relation) 三元组
    _EDGES_CTE = """
        WITH RECURSIVE edges AS (
            SELECT kc.chunk_id AS source_id,
                   rel->>'target' AS target_id,
                   COALESCE(rel->>'relation', 'related') AS relation_type
            FROM knowledge_chunks kc
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(kc.metadata->'relations') = 'array'
                     THEN kc.metadata->'relations'
                     ELSE '[]'::jsonb END
            ) AS rel
            WHERE rel->>'target' IS NOT NULL
              AND rel->>'target' <> ''
              AND (:relation_type IS NULL OR rel->>'relation' = :relation_type)
        )
    """

    _CHUNK_COLUMNS = (
        "kc.chunk_id, kc.kb_version, kc.organ, kc.content, kc.metadata, "
        "kc.source_doc_id, kc.section_path, kc.token_count"
    )

    def __init__(self, session_factory: Any = None) -> None:
        self._session_factory = session_factory

    async def _enabled(self) -> bool:
        from app.config.settings import settings

        return bool(settings.graph_query_enabled)

    async def _open_session(self):
        if self._session_factory is not None:
            session = self._session_factory()
            if hasattr(session, "__aenter__"):
                return session
            return _SessionContext(session)
        from app.config.database import AsyncSessionLocal

        return AsyncSessionLocal()

    async def query_neighbors(
        self,
        chunk_id: str,
        relation_type: Optional[str] = None,
        max_depth: int = 1,
    ) -> List[KnowledgeChunk]:
        """返回 depth <= max_depth 的可达邻居块（不含起点自身）。"""
        if not await self._enabled():
            logger.debug("graph_query_disabled", method="query_neighbors")
            return []
        if not chunk_id:
            return []

        depth = max(1, int(max_depth))
        sql = f"""
            {self._EDGES_CTE}
            , walk AS (
                SELECT e.source_id, e.target_id, e.relation_type, 1 AS depth,
                       ARRAY[e.source_id, e.target_id] AS path
                FROM edges e
                WHERE e.source_id = :chunk_id
                UNION ALL
                SELECT e.source_id, e.target_id, e.relation_type, w.depth + 1,
                       w.path || e.target_id
                FROM edges e
                JOIN walk w ON e.source_id = w.target_id
                WHERE w.depth < :max_depth
                  AND NOT e.target_id = ANY(w.path)
            )
            SELECT DISTINCT {self._CHUNK_COLUMNS}
            FROM walk w
            JOIN knowledge_chunks kc ON kc.chunk_id = w.target_id
        """
        rows = await self._fetch(
            sql,
            {"chunk_id": chunk_id, "relation_type": relation_type, "max_depth": depth},
            method="query_neighbors",
        )
        return [
            chunk for chunk in (_row_to_chunk(row) for row in rows) if chunk is not None
        ]

    async def traverse_path(
        self,
        from_chunk_id: str,
        to_chunk_id: str,
        max_hops: int = 5,
    ) -> List[KnowledgeChunk]:
        """返回从起点到终点的路径上的块（按深度升序）。"""
        if not await self._enabled():
            logger.debug("graph_query_disabled", method="traverse_path")
            return []
        if not from_chunk_id or not to_chunk_id:
            return []

        hops = max(1, int(max_hops))
        sql = f"""
            {self._EDGES_CTE}
            , walk AS (
                SELECT e.source_id, e.target_id, e.relation_type, 1 AS depth,
                       ARRAY[e.source_id, e.target_id] AS path
                FROM edges e
                WHERE e.source_id = :from_chunk_id
                UNION ALL
                SELECT e.source_id, e.target_id, e.relation_type, w.depth + 1,
                       w.path || e.target_id
                FROM edges e
                JOIN walk w ON e.source_id = w.target_id
                WHERE w.depth < :max_hops
                  AND NOT e.target_id = ANY(w.path)
            )
            SELECT DISTINCT ON (kc.chunk_id) {self._CHUNK_COLUMNS}, w.depth
            FROM walk w
            JOIN knowledge_chunks kc ON kc.chunk_id = w.target_id
            WHERE w.target_id = :to_chunk_id
            ORDER BY kc.chunk_id, w.depth
        """
        rows = await self._fetch(
            sql,
            {
                "from_chunk_id": from_chunk_id,
                "to_chunk_id": to_chunk_id,
                "relation_type": None,
                "max_hops": hops,
            },
            method="traverse_path",
        )
        return [
            chunk for chunk in (_row_to_chunk(row) for row in rows) if chunk is not None
        ]

    _OPPOSING_RELATIONS = (("supports", "excludes"),)

    async def check_contradictions(
        self,
        chunk_ids: List[str],
    ) -> List[tuple[str, str, str]]:
        """找出对同一目标持相反关系的块对，返回 ``(target_id, a, b)``。

        矛盾判据只取"明确对立"的关系对（supports vs excludes），不做语义推断——
        推断出来的"矛盾"无法向医生解释，只会制造噪声。
        """
        if not await self._enabled():
            logger.debug("graph_query_disabled", method="check_contradictions")
            return []
        ids = [chunk_id for chunk_id in chunk_ids if chunk_id]
        if len(ids) < 2:
            return []

        sql = f"""
            {self._EDGES_CTE}
            SELECT DISTINCT e1.target_id, e1.source_id AS source_a, e2.source_id AS source_b
            FROM edges e1
            JOIN edges e2
              ON e1.target_id = e2.target_id
             AND e1.source_id < e2.source_id
             AND (
                    (e1.relation_type = :positive AND e2.relation_type = :negative)
                 OR (e1.relation_type = :negative AND e2.relation_type = :positive)
                 )
            WHERE e1.source_id = ANY(:chunk_ids)
              AND e2.source_id = ANY(:chunk_ids)
        """
        positive, negative = self._OPPOSING_RELATIONS[0]
        rows = await self._fetch(
            sql,
            {
                "chunk_ids": ids,
                "relation_type": None,
                "positive": positive,
                "negative": negative,
            },
            method="check_contradictions",
        )
        result: List[tuple[str, str, str]] = []
        for row in rows:
            mapping = _row_mapping(row)
            result.append(
                (
                    str(mapping.get("target_id", "")),
                    str(mapping.get("source_a", "")),
                    str(mapping.get("source_b", "")),
                )
            )
        return result

    async def _fetch(self, sql: str, params: dict, *, method: str) -> List[Any]:
        """执行单条查询；任何异常降级为空结果（图查询不在关键路径上）。"""
        from sqlalchemy import text

        try:
            async with await self._open_session() as session:
                result = await session.execute(text(sql), params)
                return list(result.fetchall())
        except Exception as exc:  # noqa: BLE001 - 图查询失败不得影响诊断
            logger.warning(
                "graph_query_failed",
                method=method,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []


class _SessionContext:
    """把"已经是会话对象"的注入包装成 async 上下文管理器。"""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *exc_info) -> bool:
        return False


def _row_mapping(row: Any) -> dict:
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return dict(mapping)
    if isinstance(row, dict):
        return row
    return {}


def _row_to_chunk(row: Any) -> Optional[KnowledgeChunk]:
    """行 -> ``KnowledgeChunk``；无法映射时返回 None 由调用方丢弃。

    ``KnowledgeChunk`` 要求 chunk_id/organ/content 非空。造一个"空块"塞进结果集，会在
    下游变成一条没有内容、没有来源却参与排序的幽灵候选——宁可少一条也不要脏一条。
    """
    data = _row_mapping(row)
    chunk_id = str(data.get("chunk_id") or "")
    organ = str(data.get("organ") or "")
    content = str(data.get("content") or "")
    if not (chunk_id and organ and content):
        logger.warning(
            "graph_query_row_unmappable",
            keys=sorted(data.keys()),
            chunk_id=chunk_id,
        )
        return None

    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    try:
        return KnowledgeChunk(
            chunk_id=chunk_id,
            kb_version=str(data.get("kb_version") or ""),
            organ=organ,
            content=content,
            metadata=metadata,
            source_doc_id=str(data.get("source_doc_id") or ""),
            section_path=str(data.get("section_path") or ""),
            token_count=int(data.get("token_count") or 0),
        )
    except Exception as exc:  # noqa: BLE001 - 单行脏数据不得让整次查询失败
        logger.warning("graph_query_row_invalid", chunk_id=chunk_id, error=str(exc))
        return None


__all__ = [
    "GraphQueryAdapter",
    "PgVectorGraphAdapter",
]
