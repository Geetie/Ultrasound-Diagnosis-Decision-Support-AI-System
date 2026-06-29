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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.database import AsyncSessionLocal
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
    source_dir: Path, organs_filter: Optional[List[str]]
) -> List[_SourceDocFile]:
    """Walk <source-dir>/<organ>/*.json and return source doc descriptors."""
    if not source_dir.exists() or not source_dir.is_dir():
        raise IndexBuildError(
            IndexBuildErrorCode.SOURCE_NOT_FOUND,
            f"source_dir does not exist or is not a directory: {source_dir}",
        )

    docs: List[_SourceDocFile] = []
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


class MockEmbeddingService:
    """Deterministic offline embedding service used by the CLI default path.

    Produces a 768-dim vector derived from a hash of the input text. This is
    not semantically meaningful but is stable, cheap, and unit-testable.
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
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        chunker: Optional[Chunker] = None,
        feature_extractor: Optional[FeatureExtractor] = None,
        indexer: Optional[PGVectorHybridIndexer] = None,
        embedding_service: Optional[EmbeddingService] = None,
        strategy: str = "sentence_boundary",
    ):
        self.session_factory = session_factory or AsyncSessionLocal
        self.feature_extractor = feature_extractor or FeatureExtractor()
        self.indexer = indexer or PGVectorHybridIndexer()
        self.embedding_service: EmbeddingService = (
            embedding_service  # type: ignore[assignment]
            if embedding_service is not None
            else MockEmbeddingService()  # type: ignore[assignment]
        )
        self._chunker_factory: Callable[[str], Chunker] = lambda s: (
            chunker if chunker is not None else _resolve_chunker(s)
        )
        self._default_strategy = strategy

    async def build(self, request: IndexBuildRequest) -> IndexBuildResult:
        started = time.monotonic()

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
        source_docs = _discover_source_docs(source_dir, organs_filter)
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
        buffer: List[KnowledgeChunk] = []

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
                enriched: List[KnowledgeChunk] = []
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
                    for c, emb in zip(batch, embed_response.embeddings):
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
