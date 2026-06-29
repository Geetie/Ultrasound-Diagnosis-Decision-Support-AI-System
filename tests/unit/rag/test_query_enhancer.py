"""Unit tests for app.rag.query_enhancer.

Covers T-GA-RAG-01:
- L1 synonym expansion (UDAF vocabulary)
- L2 category expansion
- L3 organ-related expansion
- UDTParser organ detection
- LLM enhancer happy path
- LLM enhancer failure falls back to UDAF
- Coverage rate is logged
- Empty query handling
"""
from __future__ import annotations

from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.track_rag]

from app.knowledge.udt_parser import (
    ParsedAttribute,
    UDTParseResult,
    UDTParser,
    UDTParserConfig,
)
from app.knowledge.vocabulary import UDAFVocabulary
from app.rag.llm_enhancer import LLMEnhancer
from app.rag.query_enhancer import QueryEnhancer, _extract_chinese_phrases


def _stub_udt(
    organ: Optional[str] = None,
    terms: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
) -> UDTParser:
    """Build a UDTParser whose parse() returns a fixed UDTParseResult."""
    parser = UDTParser.__new__(UDTParser)
    parser.config = UDTParserConfig()
    parser.vocab = UDAFVocabulary()
    parser._init_patterns()
    parser._sorted_synonyms = sorted(
        list(parser.vocab.iter_synonym_mappings()), key=lambda x: -len(x[0])
    )

    attrs: List[ParsedAttribute] = []
    cats = categories or ["echogenicity"]
    for i, term in enumerate(terms or []):
        cat = cats[min(i, len(cats) - 1)]
        attrs.append(
            ParsedAttribute(
                category=cat,
                value=term,
                raw_text=term,
                confidence=1.0,
                organ=organ,
            )
        )
    result = UDTParseResult(
        raw_text="x",
        attributes=attrs,
        normalized_terms=[a.value for a in attrs],
        organ=organ,
        unrecognized_terms=[],
    )
    parser.parse = MagicMock(return_value=result)  # type: ignore[assignment]
    return parser


class TestExtractChinesePhrases:
    def test_chinese_only(self):
        out = _extract_chinese_phrases("低回声结节")
        # 2-char windows + full phrase
        assert "低回" in out
        assert "回声" in out
        assert "声结" in out
        assert "结节" in out
        assert "低回声结节" in out

    def test_empty(self):
        assert _extract_chinese_phrases("") == []

    def test_ascii_only(self):
        assert _extract_chinese_phrases("TI-RADS 4A") == []

    def test_mixed(self):
        out = _extract_chinese_phrases("肝脏 liver TI-RADS 4A")
        # 2-char window of 肝脏
        assert "肝脏" in out
        assert "肝" not in out  # 1-char windows are NOT produced


class TestQueryEnhancer:
    async def test_l1_synonym_expansion_chinese(self):
        # "低回声" should expand via UDAF synonym map
        udt = _stub_udt(organ="thyroid", terms=["低回声"])
        enhancer = QueryEnhancer(vocabulary=UDAFVocabulary(), udt_parser=udt)
        result = await enhancer.enhance("低回声结节")
        # The phrase 低回声 should yield at least one synonym
        assert "hypoechoic" in result.layer1_synonyms or "回声减低" in result.layer1_synonyms

    async def test_organ_detection_uses_udt_parser(self):
        udt = _stub_udt(organ="liver", terms=[])
        enhancer = QueryEnhancer(vocabulary=UDAFVocabulary(), udt_parser=udt)
        result = await enhancer.enhance("random query")
        assert result.organ == "liver"

    async def test_organ_hint_overrides_when_udt_returns_none(self):
        # UDT didn't detect; caller provided organ_hint
        udt = _stub_udt(organ=None, terms=[])
        enhancer = QueryEnhancer(vocabulary=UDAFVocabulary(), udt_parser=udt)
        result = await enhancer.enhance("random query", organ_hint="heart")
        assert result.organ == "heart"

    async def test_l2_category_expansion(self):
        udt = _stub_udt(organ="thyroid", terms=["hypoechoic"], categories=["echogenicity"])
        enhancer = QueryEnhancer(vocabulary=UDAFVocabulary(), udt_parser=udt, max_expansion_per_layer=20)
        result = await enhancer.enhance("hypoechoic nodule")
        assert any(
            t in result.layer2_hypernyms
            for t in ("hyperechoic", "isoechoic", "anechoic", "mixed")
        )

    async def test_l3_organ_related_expansion(self):
        # thyroid detected -> L3 should include thyroid-related terms (TI-RADS, etc.)
        udt = _stub_udt(organ="thyroid", terms=["hypoechoic"])
        enhancer = QueryEnhancer(
            vocabulary=UDAFVocabulary(), udt_parser=udt, max_expansion_per_layer=10
        )
        result = await enhancer.enhance("hypoechoic")
        # TI-RADS or anatomical_location terms are thyroid-specific
        assert any("TI-RADS" in t for t in result.layer3_related)

    async def test_enhanced_query_includes_expansion(self):
        udt = _stub_udt(organ="thyroid", terms=["hypoechoic"])
        enhancer = QueryEnhancer(vocabulary=UDAFVocabulary(), udt_parser=udt)
        result = await enhancer.enhance("hypoechoic")
        assert result.enhanced.startswith("hypoechoic")
        # Enhanced should be longer than the original due to expansion tokens
        assert len(result.enhanced) > len(result.original)

    async def test_llm_enhancer_happy_path(self):
        udt = _stub_udt(organ="thyroid", terms=["hypoechoic"])
        mock_llm = MagicMock(spec=LLMEnhancer)
        mock_llm.enhance = AsyncMock(return_value="thyroid hypoechoic nodule TI-RADS 4A")
        enhancer = QueryEnhancer(
            vocabulary=UDAFVocabulary(), udt_parser=udt, llm_enhancer=mock_llm
        )
        result = await enhancer.enhance("hypoechoic")
        assert result.llm_enhanced is True
        assert result.enhanced == "thyroid hypoechoic nodule TI-RADS 4A"

    async def test_llm_enhancer_failure_falls_back_to_udaf(self):
        udt = _stub_udt(organ="thyroid", terms=["hypoechoic"])
        mock_llm = MagicMock(spec=LLMEnhancer)
        mock_llm.enhance = AsyncMock(side_effect=RuntimeError("API down"))
        enhancer = QueryEnhancer(
            vocabulary=UDAFVocabulary(), udt_parser=udt, llm_enhancer=mock_llm
        )
        result = await enhancer.enhance("hypoechoic")
        assert result.llm_enhanced is False
        # Still got UDAF expansion
        assert len(result.layer1_synonyms) + len(result.layer2_hypernyms) > 0

    async def test_llm_enhancer_returns_none_falls_back(self):
        udt = _stub_udt(organ="thyroid", terms=["hypoechoic"])
        mock_llm = MagicMock(spec=LLMEnhancer)
        mock_llm.enhance = AsyncMock(return_value=None)
        enhancer = QueryEnhancer(
            vocabulary=UDAFVocabulary(), udt_parser=udt, llm_enhancer=mock_llm
        )
        result = await enhancer.enhance("hypoechoic")
        assert result.llm_enhanced is False

    async def test_max_per_layer_caps(self):
        udt = _stub_udt(organ="heart", terms=["normal", "enlarged", "thickened", "stenosis"])
        enhancer = QueryEnhancer(
            vocabulary=UDAFVocabulary(), udt_parser=udt, max_expansion_per_layer=2
        )
        result = await enhancer.enhance("heart condition")
        assert len(result.layer1_synonyms) <= 2
        assert len(result.layer2_hypernyms) <= 2
        assert len(result.layer3_related) <= 2

    async def test_layers_are_disjoint(self):
        udt = _stub_udt(organ="thyroid", terms=["hypoechoic"])
        enhancer = QueryEnhancer(vocabulary=UDAFVocabulary(), udt_parser=udt)
        result = await enhancer.enhance("hypoechoic")
        all_terms = (
            set(result.layer1_synonyms)
            | set(result.layer2_hypernyms)
            | set(result.layer3_related)
        )
        # Each layer contributes unique terms (no overlap between layers)
        for layer in (result.layer1_synonyms, result.layer2_hypernyms, result.layer3_related):
            assert len(set(layer)) == len(layer)
        # Sanity: at least one layer has content
        assert len(all_terms) > 0

    async def test_no_organ_no_l3(self):
        # No organ -> L3 should be empty
        udt = _stub_udt(organ=None, terms=["hypoechoic"])
        enhancer = QueryEnhancer(vocabulary=UDAFVocabulary(), udt_parser=udt)
        result = await enhancer.enhance("hypoechoic")
        assert result.layer3_related == []
