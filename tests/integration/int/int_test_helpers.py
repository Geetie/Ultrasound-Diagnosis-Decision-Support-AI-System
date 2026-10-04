"""共用测试替身：把 ``_write_result_to_db`` 的真实依赖 patch 掉。

为什么需要这个文件
--------------------
``_write_result_to_db`` **自己** ``async with AsyncSessionLocal() as db`` 并
``DiagnosisRepository(db)``（app/services/diagnosis_pipeline.py:783）——
这是 2026-09-28 的刻意改动：生产路径只有 ``flush()`` 不 ``commit()`` 会丢写，
而 async 会话退出只 close（回滚未提交事务），所以必须显式 commit。

而 12 个集成用例仍在按**旧签名**注入 ``diagnosis_repo=mock_repo``，那个形参
早已不存在 → 全部 TypeError（HEAD 即存在的失败，非本轮引入）。

两种修法对比
------------
❌ 给函数加回 ``diagnosis_repo`` 形参：等于把生产路径改回"依赖注入仓储"的旧形态，
   与 session 治理的修复方向相反，且会让"短会话 + 显式 commit"的注释失效。
✅ 让测试 patch 真实依赖（``AsyncSessionLocal`` 与 ``DiagnosisRepository``）：
   断言仍落在真实契约上（status/confidence 由 ``_write_result_to_db`` 决定）。
"""
from __future__ import annotations

from typing import Any


class FakeSession:
    """``async with AsyncSessionLocal() as db`` 的替身。"""

    def __init__(self) -> None:
        self.committed = False
        self.closed = False
        self.added: list[Any] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.closed = True
        return False

    async def commit(self) -> None:
        self.committed = True

    async def flush(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True

    def add(self, obj: Any) -> None:
        self.added.append(obj)


def patch_write_deps(monkeypatch, repo):
    """patch ``_write_result_to_db`` 用的会话工厂与仓储类。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture。
        repo: 假仓储（通常是 ``MagicMock()``，断言它收到的 ``update_result`` kwargs）。

    Returns:
        list[FakeSession]：按创建顺序记录，便于断言"确实提交了"。
    """
    from app.services import diagnosis_pipeline as dp

    sessions: list[FakeSession] = []

    def _factory() -> FakeSession:
        s = FakeSession()
        sessions.append(s)
        return s

    monkeypatch.setattr(dp, "AsyncSessionLocal", _factory)
    monkeypatch.setattr(dp, "DiagnosisRepository", lambda db: repo)
    return sessions
