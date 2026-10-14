"""Shared tokenization utilities for RAG pipeline.

Consolidates CJK+alphanumeric tokenization logic that was duplicated in:
- app/rag/bm25_fallback.py (tokenize function)
- app/rag/query_enhancer.py (_extract_chinese_phrases function)

This module provides the simple deterministic tokenizer (CJK 2-char window)
and the UDAFMaxMatchTokenizer that uses UDAFVocabulary for forward maximum
matching per the §5.5 design spec.
"""
from __future__ import annotations

import re

# Lazy import to avoid circular dependency at module level.
# UDAFVocabulary is in app.knowledge.vocabulary which is safe to import.


# ---------------------------------------------------------------------------
# Simple CJK + alphanumeric tokenizer (baseline, no vocabulary)
# ---------------------------------------------------------------------------

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_NON_WORD_RE = re.compile(r"[^A-Za-z0-9]+")


def tokenize_simple(text: str) -> list[str]:
    """Simple CJK+alphanumeric tokenizer with 2-char window for CJK.

    This is the baseline tokenizer used when no UDAFVocabulary is available.
    It matches the original tokenize() behavior from bm25_fallback.py.

    Examples:
        "低回声结节"        -> ["低回声结节", "低回声", "回声结", "结节"]
        "TI-RADS 4A nodule" -> ["ti", "rads", "4a", "nodule"]

    CJK handling: keep the full phrase (helps exact-substring match) AND
    emit a 2-character sliding window (improves recall for partial matches).
    """
    if not text:
        return []
    text = text.lower()
    tokens: list[str] = []
    for cjk_match in _CJK_RE.finditer(text):
        cjk = cjk_match.group(0)
        tokens.append(cjk)
        # 2-char sliding window for partial-match recall
        for i in range(len(cjk) - 1):
            tokens.append(cjk[i:i + 2])
    # Replace non-alphanumeric with spaces, then split.
    ascii_cleaned = _NON_WORD_RE.sub(" ", text)
    for tok in ascii_cleaned.split():
        if tok:
            tokens.append(tok)
    return tokens


# ---------------------------------------------------------------------------
# UDAF MaxMatch Tokenizer (§5.5 design spec: UDAFMaxMatchTokenizer)
# ---------------------------------------------------------------------------

class UDAFMaxMatchTokenizer:
    """Forward maximum matching tokenizer using UDAFVocabulary.

    Per §5.5 design spec: "BM25使用 UDAFMaxMatchTokenizer（正向最大匹配+UDAF词汇表），
    不依赖pkuseg-medicine/jieba，确保主方案分词故障时BM25检索质量不受影响。"

    Algorithm:
    1. Scan input from left to right.
    2. At each position, try to match the longest known term from UDAFVocabulary.
    3. On match: emit the standard term + all its synonyms as tokens.
    4. On no match: use CJK 2-char window or split alphanumeric as fallback.
    5. This is O(n * max_term_len) which is fast for medical queries (< 100 chars).

    Args:
        vocabulary: UDAFVocabulary instance providing term normalization and synonyms.
        max_term_len: Maximum term length to attempt matching (default 10 CJK chars).
    """

    def __init__(
        self,
        vocabulary: object | None = None,
        max_term_len: int = 10,
    ):
        self._vocab = vocabulary
        self._max_term_len = max_term_len
        # Build a set of all known terms (standard + synonyms) for fast lookup.
        self._known_terms: set[str] = set()
        if vocabulary is not None:
            # UDAFVocabulary has iter_synonym_mappings() and get_synonyms().
            try:
                for syn, standard in vocabulary.iter_synonym_mappings():
                    self._known_terms.add(syn)
                    self._known_terms.add(standard)
            except Exception:  # silent-ok: 词表 API 形态不同则退化（仅少一轮同义扩展，不影响主流程）
                pass  # Graceful degradation if vocabulary API differs.

    def tokenize(self, text: str) -> list[str]:
        """Tokenize using forward maximum matching + synonym expansion."""
        if not text:
            return []
        if not self._vocab or not self._known_terms:
            return tokenize_simple(text)

        text_lower = text.lower()
        tokens: list[str] = []
        i = 0
        n = len(text_lower)

        while i < n:
            # Try longest match from max_term_len down to 2.
            matched = False
            for length in range(min(self._max_term_len, n - i), 1, -1):
                candidate = text_lower[i:i + length]
                if candidate in self._known_terms:
                    # Emit the matched term.
                    tokens.append(candidate)
                    # Also emit synonyms for expanded retrieval.
                    try:
                        normalized = self._vocab.normalize(candidate)
                        if normalized:
                            if normalized != candidate:
                                tokens.append(normalized)
                            synonyms = self._vocab.get_synonyms(normalized)
                            for syn in synonyms:
                                syn_lower = syn.lower()
                                if syn_lower != candidate and syn_lower != normalized:
                                    tokens.append(syn_lower)
                    except Exception:  # silent-ok: 同义词扩展失败即跳过该词（主 token 已产出）
                        pass  # Graceful degradation.
                    i += length
                    matched = True
                    break

            if not matched:
                # Fallback: single character, then let CJK window handle rest.
                ch = text_lower[i]
                if '\u4e00' <= ch <= '\u9fff':
                    # Collect consecutive CJK chars for window tokenization.
                    j = i
                    while j < n and '\u4e00' <= text_lower[j] <= '\u9fff':
                        j += 1
                    cjk_span = text_lower[i:j]
                    tokens.append(cjk_span)
                    for k in range(len(cjk_span) - 1):
                        tokens.append(cjk_span[k:k + 2])
                    i = j
                elif ch.isalnum():
                    # Collect consecutive alphanumeric chars.
                    j = i
                    while j < n and (text_lower[j].isalnum() and not ('\u4e00' <= text_lower[j] <= '\u9fff')):
                        j += 1
                    tokens.append(text_lower[i:j])
                    i = j
                else:
                    i += 1  # Skip non-CJK, non-alnum.

        return tokens


def extract_chinese_phrases(text: str) -> list[str]:
    """Extract Chinese phrases with 2-char sliding window + full phrases.

    CJK-only: this function only extracts Chinese character sequences,
    not alphanumeric tokens. Used by QueryEnhancer for L1 expansion
    candidates where only Chinese terms need to be expanded.

    Shared utility replacing the duplicate logic in query_enhancer.py.
    """
    if not text:
        return []
    phrases: list[str] = []
    for m in _CJK_RE.finditer(text):
        cjk = m.group(0)
        for i in range(len(cjk) - 1):
            phrases.append(cjk[i:i + 2])
        phrases.append(cjk)
    return phrases
