"""建库 embedding 接线守卫（fail-fast，禁止默认假向量）。

## 为什么有这条测试

2026-09-28 接线审计发现：`KnowledgeIndexBuilderImpl` 在未注入 `embedding_service` 时
**默认使用 `MockEmbeddingService`**（`sha256(text)` 展开 768 维、无任何语义），
而 CLI `scripts/init_knowledge_base.py` 正是走这条默认路径 —— 也就是
**默认建出的知识库不具备语义检索能力**，所有召回率/准确率指标都建立在假向量上，
且下游只表现为"效果差"而不会报错，极难归因。

同一轮还发现 `app/main.py` 用 `host=/port=` 构造 `BgeM3MedicalService`（该类只接受
`base_url`），而该构造位于 `try:` 内 → **整个 RAG 初始化从未成功**（`rag_service` 恒为 None）。
这类缺陷单测全绿也发现不了，因此本文件既测行为也测"调用形状"。
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from app.knowledge.builder import (
    KnowledgeIndexBuilderImpl,
    MockEmbeddingService,
    _resolve_real_embedding_service,
)
from app.knowledge.protocols import IndexBuildError, IndexBuildErrorCode, IndexBuildRequest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAIN_PY = PROJECT_ROOT / "app" / "main.py"
CLI_PY = PROJECT_ROOT / "scripts" / "init_knowledge_base.py"

MOCK_MODEL_ID = "mock-bge-m3-medical"


class _FailingEmbedding:
    """模拟 embedding 服务不可达。"""

    async def embed_text(self, request):
        raise ConnectionError("bge-m3 unreachable")


class _SpyIndexer:
    """记录是否被触碰 —— 用于断言探活在**破坏性操作之前**。"""

    def __init__(self) -> None:
        self.touched = False

    async def exists(self, *args, **kwargs):
        self.touched = True
        return False

    async def delete_by_version(self, *args, **kwargs):
        self.touched = True
        return 0

    async def upsert(self, *args, **kwargs):
        self.touched = True


class TestDefaultServiceIsReal:
    def test_default_resolves_real_bge_m3_not_mock(self):
        """默认路径必须拿到真实服务；Mock 不得成为默认。"""
        service = _resolve_real_embedding_service()

        assert isinstance(service, MockEmbeddingService) is False
        assert type(service).__name__ == "BgeM3MedicalService"

    def test_builder_default_is_not_mock(self):
        builder = KnowledgeIndexBuilderImpl()

        assert builder.embedding_service.__class__.__name__ != "MockEmbeddingService"

    async def test_explicit_mock_is_allowed_and_passes_probe(self):
        """Mock 仅可显式注入：此时探活通过（Mock 是确定性的，不依赖网络）。"""
        mock = MockEmbeddingService()
        builder = KnowledgeIndexBuilderImpl(embedding_service=mock)

        await builder._assert_embedding_available()  # 不抛即通过

        assert builder.embedding_service is mock


class TestFailFastOnUnavailableEmbedding:
    async def test_probe_raises_index_build_error(self):
        builder = KnowledgeIndexBuilderImpl(embedding_service=_FailingEmbedding())

        with pytest.raises(IndexBuildError) as excinfo:
            await builder._assert_embedding_available()

        assert excinfo.value.error_code == IndexBuildErrorCode.EMBEDDING_FAILED
        assert "refusing" in excinfo.value.message
        # 必须保留原因链，便于现场定位是"服务没起"还是"配置错了"
        assert "ConnectionError" in (excinfo.value.detail or "")

    async def test_build_fails_before_any_destructive_operation(self, tmp_path):
        """探活必须在清库/写库**之前**：否则会先删掉旧索引再失败。"""
        spy = _SpyIndexer()
        builder = KnowledgeIndexBuilderImpl(
            embedding_service=_FailingEmbedding(), indexer=spy
        )

        with pytest.raises(IndexBuildError):
            await builder.build(
                IndexBuildRequest(
                    kb_version="kb_v1.0.0",
                    source_dir=str(tmp_path),
                    force_rebuild=True,
                )
            )

        assert spy.touched is False, "探活失败时不得触碰索引（否则会先删后失败）"

    async def test_error_message_forbids_mock_fallback(self):
        """错误信息必须明确禁止假向量回退 —— 防止有人"顺手加个 try 兜底"。"""
        builder = KnowledgeIndexBuilderImpl(embedding_service=_FailingEmbedding())

        with pytest.raises(IndexBuildError) as excinfo:
            await builder._assert_embedding_available()

        combined = f"{excinfo.value.message}{excinfo.value.detail or ''}".lower()
        assert "mock" in combined


class TestCliDefaultPath:
    """CLI 默认路径不得产出假向量。"""

    @pytest.fixture(scope="class")
    def cli_module(self):
        spec = importlib.util.spec_from_file_location(
            "_init_knowledge_base_for_test", CLI_PY
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_allow_mock_flag_defaults_to_false(self, cli_module):
        args = cli_module.parse_args(
            ["--kb-version", "kb_v1.0.0", "--source-dir", "."]
        )

        assert args.allow_mock_embedding is False

    def test_allow_mock_flag_can_be_enabled_explicitly(self, cli_module):
        args = cli_module.parse_args(
            ["--kb-version", "kb_v1.0.0", "--source-dir", ".", "--allow-mock-embedding"]
        )

        assert args.allow_mock_embedding is True

    def test_mock_is_only_injected_under_the_flag(self):
        """源码级断言：MockEmbeddingService 只出现在开关分支内。"""
        source = CLI_PY.read_text(encoding="utf-8")

        assert "if args.allow_mock_embedding:" in source
        # 注入点必须在开关分支之后（同一 if 块内），不允许无条件注入
        flag_pos = source.index("if args.allow_mock_embedding:")
        mock_pos = source.index("MockEmbeddingService()")
        assert mock_pos > flag_pos


class TestRagEmbeddingConstructorShape:
    """回归守卫：RAG 初始化块的服务构造必须用 `base_url`。

    缺陷面貌：`main.py` 传 `host=/port=`，而 `BgeM3MedicalService`/`UltrasoundClipService`
    只接受 `base_url` → TypeError 被外层 try 吞掉 → 整个 RAG 从未初始化。
    """

    def test_services_only_accept_base_url(self):
        from app.embedding.bge_m3 import BgeM3MedicalService
        from app.embedding.ultrasound_clip import UltrasoundClipService

        for service_cls in (BgeM3MedicalService, UltrasoundClipService):
            params = service_cls.__init__.__code__.co_varnames[
                : service_cls.__init__.__code__.co_argcount
            ]
            assert "base_url" in params
            assert "host" not in params, f"{service_cls.__name__} 不接受 host"
            assert "port" not in params, f"{service_cls.__name__} 不接受 port"

    def test_main_constructs_them_with_base_url(self):
        source = MAIN_PY.read_text(encoding="utf-8")

        for service_name in ("BgeM3MedicalService", "UltrasoundClipService"):
            call = re.search(
                rf"{service_name}\((.*?)\)", source, flags=re.DOTALL
            )
            assert call is not None, f"main.py 未构造 {service_name}"
            args = call.group(1)
            assert "base_url=" in args, f"{service_name} 必须用 base_url 构造"
            assert "host=" not in args, f"{service_name} 不能用 host= 构造（会 TypeError）"
