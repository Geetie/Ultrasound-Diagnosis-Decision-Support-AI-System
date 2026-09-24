"""T-GA-RAG-01: Query enhancement with 3-layer UDAF expansion (+ optional LLM).

This module takes a raw user query and produces an enhanced query string
optimized for retrieval. The enhancement pipeline is:

  L1: Synonyms (UDAF vocabulary)
  L2: Hypernyms / category co-membership (same attribute category)
  L3: Related terms in the same organ (anatomical / functional neighbors)

The LLM (Agnes) is OPTIONAL. If configured and reachable, it rewrites
the UDAF-expanded query into a more natural search query. If the LLM call
fails for any reason, the result is the deterministic UDAF expansion
(llm_enhanced=False).

The UDTParser is reused (NOT re-implemented) for organ detection and
term normalization.
"""
from __future__ import annotations

import re
from typing import List, Optional, Set

import structlog

from app.knowledge.udt_parser import UDTParser
from app.knowledge.vocabulary import UDAFVocabulary
from app.rag.llm_enhancer import LLMEnhancer
from app.rag.protocols import EnhancedQuery

logger = structlog.get_logger("app.rag.query_enhancer")


# CJK token boundary (used to extract Chinese "words" from continuous text
# when collecting L1 candidates from raw query).
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _extract_chinese_phrases(query: str) -> List[str]:
    """Return list of 2-char CJK windows + the full phrase from the query.

    CJK tokenization is done in 2-character sliding windows to surface
    partial matches (e.g. "低回声" inside "低回声结节") for L1 expansion.
    The full phrase is included as the last element to preserve exact-match
    coverage.
    """
    phrases: List[str] = []
    for m in _CJK_RE.finditer(query):
        cjk = m.group(0)
        for i in range(len(cjk) - 1):
            phrases.append(cjk[i:i + 2])
        phrases.append(cjk)
    return phrases


class QueryEnhancer:
    """T-GA-RAG-01 query enhancer.

    Combines 3-layer UDAF expansion with an optional LLM rewrite.

    Args:
        vocabulary: UDAF vocabulary (used for L1/L2/L3 expansion).
        udt_parser: UDT parser (used for organ detection + term extraction).
        llm_enhancer: Optional LLM enhancer (Agnes). None disables LLM path.
        max_expansion_per_layer: Cap on terms added per layer (avoids query
            blowing up for highly verbose UDAF organs like heart).
    """

    def __init__(
        self,
        vocabulary: UDAFVocabulary,
        udt_parser: UDTParser,
        llm_enhancer: Optional[LLMEnhancer] = None,
        max_expansion_per_layer: int = 5,
    ):
        self.vocab = vocabulary
        self.udt_parser = udt_parser
        self.llm_enhancer = llm_enhancer
        self.max_per_layer = max_expansion_per_layer

    async def enhance(
        self,
        query: str,
        organ_hint: Optional[str] = None,
        use_llm: bool = True,
    ) -> EnhancedQuery:
        """Run the full enhancement pipeline.

        Args:
            query: Raw user query.
            organ_hint: Optional organ hint from caller (overrides UDT detection).
            use_llm: 是否允许 LLM 改写（T-GB-AGT-02 Adaptive RAG 档位控制）。
                ``False`` 时只做确定性的 UDAF 三层扩展，适用于标准路径。
                默认 ``True``，与引入该参数前的行为一致。

        Returns:
            EnhancedQuery with original/enhanced text, organ, and layer breakdowns.
        """
        # Step 1: UDT parse for organ + normalized terms (re-uses existing logic).
        parse_result = self.udt_parser.parse(query, organ=organ_hint)
        organ = parse_result.organ or organ_hint
        normalized_terms: List[str] = [str(a.value) for a in parse_result.attributes]

        # Coverage is the share of detected normalized terms that the UDAF vocab knows.
        coverage = self.vocab.coverage_rate(normalized_terms) if normalized_terms else 1.0
        logger.info(
            "query_enhancement_coverage",
            coverage=round(coverage, 3),
            normalized_terms=normalized_terms,
            organ=organ,
        )

        # Step 2: L1 synonyms. Include Chinese phrases from raw query (not just
        # normalized_terms) so the synonym lookup runs on every potentially
        # useful input term.
        seed_terms = set(normalized_terms)
        for phrase in _extract_chinese_phrases(query):
            seed_terms.add(phrase)

        l1_synonyms: List[str] = []
        seen_l1: Set[str] = set()
        for term in seed_terms:
            for syn in self.vocab.get_synonyms(term):
                if syn in seen_l1:
                    continue
                seen_l1.add(syn)
                l1_synonyms.append(syn)
                if len(l1_synonyms) >= self.max_per_layer:
                    break
            if len(l1_synonyms) >= self.max_per_layer:
                break

        # Step 3: L2 hypernyms. Use the category of the first detected attribute;
        # fall back to collecting terms from all categories we know about.
        l2_hypernyms: List[str] = []
        seen_l2: Set[str] = set()
        categories: Set[str] = set()
        for attr in parse_result.attributes:
            if attr.category:
                categories.add(attr.category)
        # Always include a few baseline categories so even an organ-only query
        # gets some semantic breadth.
        baseline_categories = ["echogenicity", "boundary", "morphology", "calcification"]
        categories.update(baseline_categories)
        for cat in categories:
            for term in self.vocab.get_terms_by_category(cat):
                if term in seen_l2:
                    continue
                if term in seed_terms or term in l1_synonyms:
                    continue
                seen_l2.add(term)
                l2_hypernyms.append(term)
                if len(l2_hypernyms) >= self.max_per_layer:
                    break
            if len(l2_hypernyms) >= self.max_per_layer:
                break

        # Step 4: L3 organ-related terms. If an organ was detected, include
        # other terms registered for that organ (TI-RADS, anatomical locations,
        # heart conditions, etc.). These are highly relevant siblings.
        l3_related: List[str] = []
        seen_l3: Set[str] = set()
        if organ:
            for term in self.vocab.get_terms_by_organ(organ):
                if term in seen_l3:
                    continue
                if term in seed_terms or term in l1_synonyms or term in l2_hypernyms:
                    continue
                seen_l3.add(term)
                l3_related.append(term)
                if len(l3_related) >= self.max_per_layer:
                    break

        # Step 5: Compose enhanced query string.
        expansion_tokens = l1_synonyms + l2_hypernyms + l3_related
        enhanced_udaf = query if not expansion_tokens else f"{query} " + " ".join(expansion_tokens)

        # Step 6: Optional LLM rewrite.
        # T-GB-AGT-02：标准路径（``use_llm_enhancement=False``）跳过 LLM
        # 改写，只保留确定性的 UDAF 扩展 —— 这是"标准路径更快"的实质来源。
        llm_enhanced = False
        enhanced_final = enhanced_udaf
        if self.llm_enhancer is not None and use_llm:
            try:
                rewritten = await self.llm_enhancer.enhance(
                    original_query=query,
                    udaf_expansion=expansion_tokens,
                    organ=organ,
                )
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "llm_enhance_raised_exception",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                rewritten = None
            if rewritten:
                enhanced_final = rewritten
                llm_enhanced = True
                logger.info(
                    "query_enhancement_llm_applied",
                    original_len=len(query),
                    enhanced_len=len(enhanced_final),
                )
            else:
                logger.info("query_enhancement_llm_fell_back_to_udaf")

        return EnhancedQuery(
            original=query,
            enhanced=enhanced_final,
            organ=organ,
            layer1_synonyms=l1_synonyms,
            layer2_hypernyms=l2_hypernyms,
            layer3_related=l3_related,
            llm_enhanced=llm_enhanced,
        )


__all__ = ["QueryEnhancer", "_extract_chinese_phrases"]
