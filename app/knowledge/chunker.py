"""
Knowledge Base Chunker

Splits source documents into chunks suitable for embedding and indexing.
Three strategies are supported:
- fixed_window: split by character count, simple and predictable.
- sentence_boundary: split at sentence/paragraph boundaries, keep chunks in
                    256-512 token range. Default strategy.
- semantic: split at clinical semantic boundaries detected by UDTParser
            (TI-RADS / LI-RADS / Category titles).

All strategies emit KnowledgeChunk objects with unique chunk_id and section_path.
Token count is a heuristic len(text)//2 (mixed Chinese/English).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

import structlog

from app.knowledge.protocols import KnowledgeChunk

logger = structlog.get_logger("app.knowledge.chunker")


# Default token range. Lower bound avoids over-fragmentation; upper bound
# keeps each chunk well within bge-m3-medical's 512-token context.
DEFAULT_MIN_TOKENS = 256
DEFAULT_MAX_TOKENS = 512
DEFAULT_WINDOW_CHARS = 1000  # ~ 500 tokens heuristic


# Heuristic token count: 1 token ~ 2 characters for mixed CJK + Latin.
def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 2)


def _build_chunk_id(source_doc_id: str, chunk_index: int) -> str:
    return f"{source_doc_id}#{chunk_index:04d}"


@dataclass
class KnowledgeSourceDoc:
    """Lightweight source document descriptor passed to chunkers.

    Attributes:
        source_doc_id: Stable identifier for the source document.
        organ: Target organ (e.g. "thyroid").
        content: Full raw text of the document.
        section_path: Optional pre-existing section path (e.g. "TI-RADS/Category 4").
                      If empty, chunker will derive a path.
    """
    source_doc_id: str
    organ: str
    content: str
    section_path: str = ""


class ChunkerStrategy(str, Enum):
    """Chunking strategy selector."""
    FIXED_WINDOW = "fixed_window"
    SENTENCE_BOUNDARY = "sentence_boundary"
    SEMANTIC = "semantic"


class Chunker(Protocol):
    """Protocol for chunker implementations."""
    def chunk(self, doc: KnowledgeSourceDoc, kb_version: str) -> list[KnowledgeChunk]:
        ...


# Sentence-boundary regex: CJK + Latin punctuation + newlines.
# CJK full-width punctuation terminates a sentence by itself; Latin
# punctuation needs a trailing whitespace to avoid splitting abbreviations.
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?])\s+|(?<=[,;])\s+|(?<=[。！？])|(?<=[，；])|\n+",
)


# Semantic boundary: TI-RADS / LI-RADS / Category / Subcategory titles
# detected by the chunker itself (avoid coupling chunker to UDTParser at import
# time for the simple strategies).
_SEMANTIC_TITLE_RE = re.compile(
    r"(?:^|\n)\s*("
    r"TI[-\s]?RADS[-\s]?\d[ABC]?"
    r"|LI[-\s]?RADS[-\s]?\d[MTR]?"
    r"|LR[-\s]?\d[MTR]?"
    r"|Category\s+\d[ABC]?"
    r"|Subcategory\s+\d[ABC]?"
    r"|\d+类"
    r")\b[^\n]*",
    re.IGNORECASE,
)


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences on common CJK + Latin punctuation.

    Empty fragments are dropped. Whitespace is preserved within a sentence.
    """
    if not text:
        return []
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _to_chunk(
    source_doc_id: str,
    kb_version: str,
    organ: str,
    content: str,
    section_path: str,
    chunk_index: int,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=_build_chunk_id(source_doc_id, chunk_index),
        kb_version=kb_version,
        organ=organ,
        content=content.strip(),
        metadata={"strategy": "chunker", "chunk_index": chunk_index},
        source_doc_id=source_doc_id,
        section_path=section_path,
        token_count=_approx_tokens(content),
    )


class FixedWindowChunker:
    """Splits by fixed character window. Predictable, no semantic awareness.

    Use as a deterministic fallback when the other strategies are unsuitable
    (e.g. very short, punctuation-poor text).
    """

    def __init__(self, window_chars: int = DEFAULT_WINDOW_CHARS):
        if window_chars <= 0:
            raise ValueError("window_chars must be positive")
        self.window_chars = window_chars

    def chunk(self, doc: KnowledgeSourceDoc, kb_version: str) -> list[KnowledgeChunk]:
        text = doc.content or ""
        if not text.strip():
            return []

        chunks: list[KnowledgeChunk] = []
        stride = self.window_chars
        base_section = doc.section_path or doc.organ

        # Iterate full windows, then capture the tail if any remainder exists.
        for i, start in enumerate(range(0, len(text), stride)):
            piece = text[start : start + stride]
            if not piece.strip():
                continue
            chunks.append(_to_chunk(
                source_doc_id=doc.source_doc_id,
                kb_version=kb_version,
                organ=doc.organ,
                content=piece,
                section_path=base_section,
                chunk_index=i,
            ))

        if not chunks:
            # Defensive: if the entire text is shorter than stride we still
            # emit a single chunk above; this branch is just safety.
            chunks.append(_to_chunk(
                source_doc_id=doc.source_doc_id,
                kb_version=kb_version,
                organ=doc.organ,
                content=text,
                section_path=base_section,
                chunk_index=0,
            ))
        return chunks


class SentenceBoundaryChunker:
    """Splits at sentence boundaries; targets 256-512 tokens per chunk.

    - Tokens are counted heuristically (len(text)//2).
    - If a single sentence exceeds MAX_TOKENS * 2 characters, it is split
      using FixedWindowChunker (window=MAX_TOKENS*2).
    - The last accumulated chunk shorter than MIN_TOKENS is merged into the
      previous chunk to avoid over-fragmentation.
    """

    def __init__(
        self,
        min_tokens: int = DEFAULT_MIN_TOKENS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        if min_tokens <= 0 or max_tokens <= 0:
            raise ValueError("min_tokens and max_tokens must be positive")
        if min_tokens > max_tokens:
            raise ValueError("min_tokens must be <= max_tokens")
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens
        # 1 token ~ 2 chars heuristic.
        self.max_chars = max_tokens * 2
        self.fallback = FixedWindowChunker(window_chars=self.max_chars)

    def chunk(self, doc: KnowledgeSourceDoc, kb_version: str) -> list[KnowledgeChunk]:
        text = doc.content or ""
        if not text.strip():
            return []

        sentences = _split_sentences(text)
        if not sentences:
            return []

        base_section = doc.section_path or doc.organ
        chunks: list[KnowledgeChunk] = []
        buffer: list[str] = []
        buffer_chars = 0

        def _flush_buffer(idx_hint: int) -> KnowledgeChunk | None:
            if not buffer:
                return None
            content = " ".join(buffer).strip()
            buffer.clear()
            return _to_chunk(
                source_doc_id=doc.source_doc_id,
                kb_version=kb_version,
                organ=doc.organ,
                content=content,
                section_path=base_section,
                chunk_index=idx_hint,
            )

        next_index = 0
        for sentence in sentences:
            if len(sentence) > self.max_chars:
                # Flush current buffer first, then split oversized sentence.
                if buffer:
                    emitted = _flush_buffer(next_index)
                    if emitted is not None:
                        chunks.append(emitted)
                        next_index += 1
                sub_doc = KnowledgeSourceDoc(
                    source_doc_id=doc.source_doc_id,
                    organ=doc.organ,
                    content=sentence,
                    section_path=base_section,
                )
                for sub in self.fallback.chunk(sub_doc, kb_version):
                    # Re-stamp chunk index to keep monotonic sequence.
                    new_chunk = sub.model_copy(update={"chunk_index": next_index})
                    new_id = _build_chunk_id(doc.source_doc_id, next_index)
                    new_chunk = new_chunk.model_copy(update={"chunk_id": new_id})
                    chunks.append(new_chunk)
                    next_index += 1
                buffer_chars = 0
                continue

            projected = buffer_chars + len(sentence) + 1  # +1 for space
            if projected > self.max_chars and buffer:
                emitted = _flush_buffer(next_index)
                if emitted is not None:
                    chunks.append(emitted)
                    next_index += 1
                buffer_chars = 0
            buffer.append(sentence)
            buffer_chars += len(sentence) + 1

        # Flush remainder.
        tail = _flush_buffer(next_index)
        if tail is not None:
            chunks.append(tail)
            next_index += 1

        # Merge trailing short chunk into previous one (avoid tiny fragments).
        if len(chunks) >= 2 and chunks[-1].token_count < self.min_tokens:
            last = chunks[-1]
            prev = chunks[-2]
            merged_content = (prev.content + " " + last.content).strip()
            merged = _to_chunk(
                source_doc_id=doc.source_doc_id,
                kb_version=kb_version,
                organ=doc.organ,
                content=merged_content,
                section_path=prev.section_path or base_section,
                chunk_index=prev.metadata.get("chunk_index", 0),
            )
            chunks = chunks[:-2] + [merged]

        # Re-stamp chunk indices sequentially to guarantee monotonic order
        # regardless of merge/oversized-split.
        for i, c in enumerate(chunks):
            c.chunk_id = _build_chunk_id(doc.source_doc_id, i)
            c.metadata["chunk_index"] = i

        return chunks


class SemanticChunker:
    """Splits at clinical semantic boundaries (TI-RADS / LI-RADS / Category).

    Strategy:
    - Detect titles via regex (TI-RADS X, LI-RADS X, Category X, etc.).
    - Split into sections: each section is from one title to the next.
    - For each section, run SentenceBoundaryChunker to further refine.
    - Unmatched trailing text is appended to the last section or emitted as
      a section-less chunk.
    """

    def __init__(
        self,
        min_tokens: int = DEFAULT_MIN_TOKENS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        self.inner = SentenceBoundaryChunker(
            min_tokens=min_tokens, max_tokens=max_tokens
        )

    def chunk(self, doc: KnowledgeSourceDoc, kb_version: str) -> list[KnowledgeChunk]:
        text = doc.content or ""
        if not text.strip():
            return []

        base_section = doc.section_path or doc.organ
        matches = list(_SEMANTIC_TITLE_RE.finditer(text))
        if not matches:
            # No semantic boundaries; fall back to sentence splitter on full doc.
            return self.inner.chunk(doc, kb_version)

        sections: list[tuple[str, str]] = []  # (title, body)
        cursor = 0
        for i, m in enumerate(matches):
            title = m.group(1).strip()
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            if start > cursor:
                prelude = text[cursor:start].strip()
                if prelude:
                    sections.append(("", prelude))
            sections.append((title, text[start:end].strip()))
            cursor = end
        if cursor < len(text):
            tail = text[cursor:].strip()
            if tail:
                sections.append(("", tail))

        # Build chunks per section.
        all_chunks: list[KnowledgeChunk] = []
        for title, body in sections:
            if not body:
                continue
            section_path = f"{base_section}/{title}" if title else base_section
            sub_doc = KnowledgeSourceDoc(
                source_doc_id=doc.source_doc_id,
                organ=doc.organ,
                content=body,
                section_path=section_path,
            )
            for c in self.inner.chunk(sub_doc, kb_version):
                # Re-stamp section_path (inner chunker used its own section_path
                # which is the same as ours for the sub_doc, so this is a no-op
                # except in the merge-tail path).
                c.section_path = section_path
                all_chunks.append(c)

        # Re-stamp chunk indices to keep them monotonic across sections.
        for i, c in enumerate(all_chunks):
            c.chunk_id = _build_chunk_id(doc.source_doc_id, i)
            c.metadata["chunk_index"] = i
            c.metadata["semantic_title"] = True
        return all_chunks


def create_chunker(strategy: ChunkerStrategy | str) -> Chunker:
    """Factory: return a chunker for the given strategy name or enum."""
    if isinstance(strategy, str):
        try:
            strategy = ChunkerStrategy(strategy)
        except ValueError as e:
            raise ValueError(
                f"Unknown chunking strategy: {strategy!r}. "
                f"Valid options: {[s.value for s in ChunkerStrategy]}"
            ) from e

    if strategy == ChunkerStrategy.FIXED_WINDOW:
        return FixedWindowChunker()
    if strategy == ChunkerStrategy.SENTENCE_BOUNDARY:
        return SentenceBoundaryChunker()
    if strategy == ChunkerStrategy.SEMANTIC:
        return SemanticChunker()
    raise ValueError(f"Unhandled strategy: {strategy}")
