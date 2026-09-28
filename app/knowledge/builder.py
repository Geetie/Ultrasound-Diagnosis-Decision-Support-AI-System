"""
Knowledge Base Index Builder

Top-level orchestrator that ties together chunking, feature extraction,
embedding, and indexing. Wires the pipeline:

    source doc -> chunker -> feature_extractor -> embedding_service -> indexer

Supports:
- Per-organ filtering
- Force rebuild (delete-then-write for a given kb_version)
- Resume / idempotent upsert on chunk_id
- Organ-level chunk counts in the result

The default source layout is:
    <source-dir>/
        manifest.json            # {"kb_version": "kb_v1.0.0", "embedding_model": "bge-m3-medical"}
        <organ>/<doc>.json       # {"source_doc_id": "...", "title": "...", "content": "..."}
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.database import AsyncSessionLocal
from app.config.settings import settings
from app.embedding.bge_m3 import BgeM3MedicalService
from app.embedding.protocols import (
    EmbeddingResponse,
    EmbeddingService,
    TextEmbeddingRequest,
)
from app.knowledge.chunker import (
    Chunker,
    ChunkerStrategy,
    KnowledgeSourceDoc,
    create_chunker,
)
from app.knowledge.desensitization import (
    Desensitizer,
    get_default_desensitizer,
)
from app.knowledge.feature_extractor import FeatureExtractor
from app.knowledge.hybrid_indexer import PGVectorHybridIndexer
from app.knowledge.protocols import (
    IndexBuildError,
    IndexBuildErrorCode,
    IndexBuildRequest,
    IndexBuildResult,
    KnowledgeChunk,
    KnowledgeIndexBuilder,
)
from app.knowledge.version import parse_kb_version

logger = structlog.get_logger("app.knowledge.builder")


@dataclass
class _SourceDocFile:
    source_doc_id: str
    organ: str
    content: str
    section_path: str = ""


def _load_manifest(source_dir: Path) -> dict:
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.exists():
        raise IndexBuildError(
            IndexBuildErrorCode.SOURCE_NOT_FOUND,
            f"manifest.json not found under {source_dir}",
            detail=f"expected file: {manifest_path}",
        )
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise IndexBuildError(
            IndexBuildErrorCode.INVALID_MANIFEST,
            f"manifest.json is not valid JSON: {e}",
        ) from e
    if not isinstance(data, dict):
        raise IndexBuildError(
            IndexBuildErrorCode.INVALID_MANIFEST,
            "manifest.json root must be an object",
        )
    if "kb_version" not in data:
        raise IndexBuildError(
            IndexBuildErrorCode.INVALID_MANIFEST,
            "manifest.json missing required field: kb_version",
        )
    return data


def _discover_source_docs(
    source_dir: Path,
    organs_filter: list[str] | None,
    desensitizer: Desensitizer | None = None,
) -> list[_SourceDocFile]:
    """Walk <source-dir>/<organ>/*.json and return source doc descriptors.

    Args:
        source_dir: 知识库源文档根目录。
        organs_filter: 仅保留这些器官子目录（None 表示全部）。
        desensitizer: 入库前脱敏器（P0-RAG-03）。传入时对每篇文档调用
            ``desensitize(content, source_kind)``；``skipped=True`` 的来源
            （如 ``guidelines``，属公开知识）保留原文，其余使用脱敏文本。
            None 表示不做脱敏（保持既有行为）。
    """
    if not source_dir.exists() or not source_dir.is_dir():
        raise IndexBuildError(
            IndexBuildErrorCode.SOURCE_NOT_FOUND,
            f"source_dir does not exist or is not a directory: {source_dir}",
        )

    docs: list[_SourceDocFile] = []
    for organ_dir in sorted(p for p in source_dir.iterdir() if p.is_dir()):
        organ = organ_dir.name
        if organs_filter and organ not in organs_filter:
            continue
        for json_file in sorted(organ_dir.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                logger.warning(
                    "skip_invalid_source_json",
                    path=str(json_file),
                    error=str(e),
                )
                continue
            if not isinstance(data, dict):
                logger.warning("skip_non_object_source", path=str(json_file))
                continue
            content = data.get("content", "")
            if not isinstance(content, str) or not content.strip():
                logger.warning(
                    "skip_empty_content_source", path=str(json_file)
                )
                continue
            source_doc_id = data.get("source_doc_id") or json_file.stem
            section_path = data.get("section_path", "")
            if desensitizer is not None:
                source_kind = data.get("source_kind") or "case_reports"
                sanitized = desensitizer.desensitize(content, source_kind)
                if not sanitized.skipped:
                    content = sanitized.sanitized
            docs.append(
                _SourceDocFile(
                    source_doc_id=source_doc_id,
                    organ=organ,
                    content=content,
                    section_path=section_path,
                )
            )
    return docs


def _resolve_chunker(strategy: str) -> Chunker:
    try:
        return create_chunker(ChunkerStrategy(strategy))
    except ValueError:
        return create_chunker(ChunkerStrategy.SENTENCE_BOUNDARY)


def _resolve_real_embedding_service() -> EmbeddingService:
    """构造**真实** embedding 服务（BgeM3）。

    ## 为什么默认不再是 Mock（2026-09-28 接线审计）

    本模块此前在未注入 `embedding_service` 时默认返回 `MockEmbeddingService`
    （`sha256(text)` 展开 768 维、**无任何语义**），而 CLI `scripts/init_knowledge_base.py`
    正是走这条默认路径 —— 也就是说**默认建出的知识库不具备语义检索能力**，
    所有"召回率/准确率"指标都建立在假向量上，且下游只表现为"效果差"而非"构建失败"。

    现在：默认走真实服务；**Mock 只能显式注入**（单测或 CLI `--allow-mock-embedding`）。
    真实服务不可用时由 `_assert_embedding_available()` 在构建前抛错，**不做静默降级**。
    """
    return BgeM3MedicalService(
        base_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}",
    )


class MockEmbeddingService:
    """确定性离线 embedding 服务 —— **仅限显式注入**（单测 / CLI `--allow-mock-embedding`）。

    Produces a 768-dim vector derived from a hash of the input text. This is
    **not semantically meaningful** but is stable, cheap, and unit-testable.

    ⚠️ 任何默认路径都**不得**使用本服务：用它建出的知识库无语义检索能力，
    会污染检索召回率与一致性评估结论。
    """

    DIMENSION = 768

    async def embed_text(
        self, request: TextEmbeddingRequest
    ) -> EmbeddingResponse:
        import hashlib

        embeddings: list[list[float]] = []
        total_tokens = 0
        for text in request.texts:
            h = hashlib.sha256(text.encode("utf-8")).digest()
            # Repeat the digest to fill 768 floats; keep it deterministic.
            needed = self.DIMENSION
            buf = bytearray()
            while len(buf) < needed * 4:
                buf.extend(h)
            raw = bytes(buf[: needed * 4])
            vec = [
                ((int.from_bytes(raw[i * 4 : i * 4 + 4], "big") / 0xFFFFFFFF)
                 - 0.5)
                for i in range(needed)
            ]
            embeddings.append(vec)
            total_tokens += max(1, len(text) // 2)
        return EmbeddingResponse(
            embeddings=embeddings,
            model="mock-bge-m3-medical",
            dimension=self.DIMENSION,
            request_id=request.request_id,
            token_count=total_tokens,
        )

    async def embed_text_sparse(self, request):  # pragma: no cover
        raise NotImplementedError("MockEmbeddingService does not support sparse")

    async def health_check(self) -> bool:  # pragma: no cover
        return True

    async def get_model_info(self):  # pragma: no cover
        from app.embedding.protocols import EmbeddingModelInfo, EmbeddingModelType
        return EmbeddingModelInfo(
            model_name="mock-bge-m3-medical",
            model_type=EmbeddingModelType.BGE_M3_MEDICAL,
            dimension=self.DIMENSION,
            max_input_length=512,
        )

    async def close(self) -> None:  # pragma: no cover
        return None


class KnowledgeIndexBuilderImpl(KnowledgeIndexBuilder):
    """Concrete knowledge base index builder.

    Parameters
    ----------
    session_factory:
        async_sessionmaker producing AsyncSession instances. Defaults to the
        global AsyncSessionLocal from app.config.database.
    chunker, feature_extractor, indexer, embedding_service:
        Pipeline components. Defaults are wired to the production modules.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        chunker: Chunker | None = None,
        feature_extractor: FeatureExtractor | None = None,
        indexer: PGVectorHybridIndexer | None = None,
        embedding_service: EmbeddingService | None = None,
        strategy: str = "sentence_boundary",
    ):
        self.session_factory = session_factory or AsyncSessionLocal
        self.feature_extractor = feature_extractor or FeatureExtractor()
        self.indexer = indexer or PGVectorHybridIndexer()
        # 显式注入优先（**Mock 只能显式注入**）；否则构造真实 embedding 服务。
        self.embedding_service: EmbeddingService = (
            embedding_service
            if embedding_service is not None
            else _resolve_real_embedding_service()
        )
        self._chunker_factory: Callable[[str], Chunker] = lambda s: (
            chunker if chunker is not None else _resolve_chunker(s)
        )
        self._default_strategy = strategy

    async def _assert_embedding_available(self) -> None:
        """建库前对 embedding 服务做一次**真实探活**，不可用即失败。

        刻意**不做任何降级**：静默退回假向量会让知识库的检索能力整体失真，
        且这种失真在下游表现为"召回率偏低"而不是"构建失败"，极难归因。
        """
        try:
            await self.embedding_service.embed_text(
                TextEmbeddingRequest(texts=["embedding probe"])
            )
        except Exception as exc:
            raise IndexBuildError(
                IndexBuildErrorCode.EMBEDDING_FAILED,
                "embedding service unavailable; refusing to build the knowledge base "
                "(mock vectors are forbidden on the default path)",
                detail=f"{type(exc).__name__}: {exc}",
                retryable=True,
            ) from exc

    async def build(self, request: IndexBuildRequest) -> IndexBuildResult:
        started = time.monotonic()

        # 0. 真实 embedding 探活：必须在**任何破坏性操作**（清库/写库）之前。
        await self._assert_embedding_available()

        # 1. Validate kb_version format up front.
        kb_version_obj = parse_kb_version(request.kb_version)
        if kb_version_obj is None:
            raise IndexBuildError(
                IndexBuildErrorCode.VERSION_MISMATCH,
                f"invalid kb_version format: {request.kb_version!r} "
                "(expected kb_v{major}.{minor}.{patch})",
            )

        # 2. Load manifest and discover source docs.
        source_dir = Path(request.source_dir)
        manifest = _load_manifest(source_dir)
        manifest_version = manifest.get("kb_version")
        if manifest_version and manifest_version != request.kb_version:
            raise IndexBuildError(
                IndexBuildErrorCode.VERSION_MISMATCH,
                f"manifest kb_version ({manifest_version}) does not match "
                f"request kb_version ({request.kb_version})",
            )

        organs_filter = request.organs or None
        # P0-RAG-03：case_reports 含 PII，必须在入库前脱敏（硬约束）。
        # guidelines 等公开知识由 Desensitizer 内部跳过。
        source_docs = _discover_source_docs(
            source_dir, organs_filter, desensitizer=get_default_desensitizer()
        )
        if not source_docs:
            logger.warning(
                "no_source_docs_found",
                source_dir=str(source_dir),
                organs=organs_filter,
            )
            return IndexBuildResult(
                kb_version=request.kb_version,
                build_duration_seconds=time.monotonic() - started,
            )

        # 3. Force-rebuild cleanup.
        async with self.session_factory() as session:
            if request.force_rebuild:
                deleted = await self.indexer.delete_by_version(
                    request.kb_version, session
                )
                logger.info(
                    "force_rebuild_cleared",
                    kb_version=request.kb_version,
                    deleted=deleted,
                )

        # 4. Build chunks per source doc.
        chunker = self._chunker_factory(request.strategy or self._default_strategy)
        organ_breakdown: dict[str, int] = {}
        skipped_existing = 0
        buffer: list[KnowledgeChunk] = []

        async with self.session_factory() as session:
            for doc in source_docs:
                src = KnowledgeSourceDoc(
                    source_doc_id=doc.source_doc_id,
                    organ=doc.organ,
                    content=doc.content,
                    section_path=doc.section_path,
                )
                raw_chunks = chunker.chunk(src, request.kb_version)

                # Extract features + check resume for each chunk.
                enriched: list[KnowledgeChunk] = []
                for c in raw_chunks:
                    # Resume: skip if chunk_id already exists.
                    if await self.indexer.exists(c.chunk_id, session):
                        skipped_existing += 1
                        continue
                    features = self.feature_extractor.extract(
                        c.content, organ_hint=c.organ
                    )
                    meta = self.feature_extractor.features_to_metadata(features)
                    # Preserve chunker-supplied metadata (chunk_index, strategy)
                    # while overwriting with extracted features.
                    merged_meta = {**c.metadata, **meta}
                    c.metadata = merged_meta
                    enriched.append(c)

                if not enriched:
                    continue

                # Embed in batches.
                for start in range(0, len(enriched), request.batch_size):
                    batch = enriched[start : start + request.batch_size]
                    texts = [c.content for c in batch]
                    embed_response = await self.embedding_service.embed_text(
                        TextEmbeddingRequest(texts=texts)
                    )
                    if len(embed_response.embeddings) != len(batch):
                        raise IndexBuildError(
                            IndexBuildErrorCode.EMBEDDING_FAILED,
                            "embedding count mismatch",
                            detail=(
                                f"expected {len(batch)} embeddings, got "
                                f"{len(embed_response.embeddings)}"
                            ),
                            retryable=True,
                        )
                    for c, emb in zip(batch, embed_response.embeddings, strict=False):
                        c.embedding = list(emb) if emb is not None else None
                    await self.indexer.upsert(batch, session)

                organ_breakdown[doc.organ] = (
                    organ_breakdown.get(doc.organ, 0) + len(enriched)
                )
                buffer.extend(enriched)
                logger.info(
                    "doc_processed",
                    source_doc_id=doc.source_doc_id,
                    organ=doc.organ,
                    chunks_added=len(enriched),
                    chunks_skipped=len(raw_chunks) - len(enriched),
                    running_total=len(buffer),
                )

        total_chunks = sum(organ_breakdown.values())
        duration = time.monotonic() - started
        logger.info(
            "build_complete",
            kb_version=request.kb_version,
            total_chunks=total_chunks,
            organ_breakdown=organ_breakdown,
            skipped_existing=skipped_existing,
            duration_seconds=duration,
        )
        return IndexBuildResult(
            kb_version=request.kb_version,
            total_chunks=total_chunks,
            organ_breakdown=organ_breakdown,
            build_duration_seconds=duration,
            index_path="pg:knowledge_chunks",
            skipped_existing=skipped_existing,
        )

    async def validate(self, kb_version: str) -> bool:
        async with self.session_factory() as session:
            count = await self.indexer.count_by_version(kb_version, session)
        return count > 0
