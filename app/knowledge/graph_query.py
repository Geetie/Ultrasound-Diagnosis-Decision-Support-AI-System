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
    """默认实现: PGVector + 递归 CTE (一期骨架)。

    一期仅提供接口定义和空实现。完整的图遍历和矛盾检测
    需要二期 Neo4j 支持或 PGVector 递归 CTE 优化。

    接入条件: 见模块顶部 docstring 与 settings.graph_query_enabled。
    """

    def __init__(self, session_factory: Any = None) -> None:
        self._session_factory = session_factory

    async def query_neighbors(
        self,
        chunk_id: str,
        relation_type: Optional[str] = None,
        max_depth: int = 1,
    ) -> List[KnowledgeChunk]:
        # P2-KB-08: 一期 STUB，PGVector 递归 CTE 实现延期至二期。
        # 接入条件见 settings.graph_query_enabled + 模块 docstring。
        logger.warning(
            "graph_query_stub_query_neighbors",
            chunk_id=chunk_id,
            relation_type=relation_type,
            max_depth=max_depth,
        )
        return []

    async def traverse_path(
        self,
        from_chunk_id: str,
        to_chunk_id: str,
        max_hops: int = 5,
    ) -> List[KnowledgeChunk]:
        # P2-KB-08: 一期 STUB
        logger.warning(
            "graph_query_stub_traverse_path",
            from_chunk_id=from_chunk_id,
            to_chunk_id=to_chunk_id,
            max_hops=max_hops,
        )
        return []

    async def check_contradictions(
        self,
        chunk_ids: List[str],
    ) -> List[tuple[str, str, str]]:
        # P2-KB-08: 一期 STUB
        logger.warning(
            "graph_query_stub_check_contradictions",
            chunk_count=len(chunk_ids),
        )
        return []


__all__ = [
    "GraphQueryAdapter",
    "PgVectorGraphAdapter",
]
