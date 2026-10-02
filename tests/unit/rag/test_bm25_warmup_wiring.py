"""守卫：BM25 索引必须被**真实调用**构建，否则检索的稀疏腿静默为空。

## 为什么需要它（2026-09-28 真机部署暴露）

`BM25Index` 是懒构建的，`health_check()` 只看 `_built is not None and bm25 is not None`
（`app/rag/bm25_fallback.py`）；而 **app/ 生产代码里从来没有任何地方调用
`ensure_built()`** —— 只有测试调用过。于是真机上：

    rag_health_partial_failure bm25=False embedding=True reranker=True vector_index=True

即：嵌入与重排修好之后，稀疏腿仍然是空的（检索只靠向量 + 重排），
而所有单测全绿 —— 又一例"能力已实现、但从未被生产链路调用"。

这里钉两件事：① 启动期确实调用了 `ensure_built`；② 调用之后健康检查才可能为真。
"""
from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
MAIN_PY = REPO_ROOT / "app" / "main.py"


def _lifespan_source() -> str:
    source = MAIN_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "lifespan":
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError("未在 app/main.py 找到 lifespan（改名后请同步本守卫）")


class TestBm25IsBuiltAtStartup:
    def test_lifespan里调用了ensure_built(self):
        assert "ensure_built(" in _lifespan_source(), (
            "BM25Index 必须被显式构建；否则 rag_health.bm25 恒 False、稀疏腿恒空"
        )

    def test_预热失败不阻断启动(self):
        """预热必须包在 try 里：知识库为空或 DB 异常都不该让主服务起不来。"""
        body = _lifespan_source()
        index = body.find("ensure_built(")
        assert index != -1
        window = body[max(0, index - 400) : index]
        assert "try:" in window, "ensure_built 必须包在 try 内（best-effort 预热）"


class TestBm25HealthSemantics:
    """健康布尔来自 `_built`，所以"没构建"必须为 False、"构建过"才为 True。"""

    def test_未构建时为False(self):
        import asyncio

        from app.rag.bm25_fallback import BM25Index

        index = BM25Index(session_factory=lambda: None)  # type: ignore[arg-type]

        assert asyncio.run(index.health_check()) is False

    def test_构建后为True(self):
        import asyncio

        from app.rag.bm25_fallback import BM25Index

        class _Row:
            def __init__(self, cid: str, content: str) -> None:
                self.chunk_id = cid
                self.kb_version = "kb_v1.0.0"
                self.organ = "thyroid"
                self.content = content
                self.meta = {}
                self.embedding = None
                # BM25Index 构建时还会读这几个字段（缺了会 AttributeError）
                self.source_doc_id = f"doc-{cid}"
                self.section_path = ""
                self.token_count = len(content)

        class _Result:
            def __init__(self, rows) -> None:
                self._rows = rows

            def all(self):
                return self._rows

        class _Session:
            async def execute(self, *_args, **_kwargs):
                return _Result(
                    [
                        _Row("c1", "低回声结节 边界清晰"),
                        _Row("c2", "TI-RADS 4A 建议穿刺"),
                    ]
                )

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        index = BM25Index(session_factory=lambda: _Session())  # type: ignore[arg-type]

        async def _scenario() -> bool:
            await index.ensure_built()
            return await index.health_check()

        assert asyncio.run(_scenario()) is True
