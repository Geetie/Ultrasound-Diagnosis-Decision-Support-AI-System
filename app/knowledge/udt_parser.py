"""
UDT (Unified Diagnostic Taxonomy) Parser
Parses and normalizes diagnostic text using UDAF vocabulary.

TODO(Phase0A): DESIGN-005 - Add UDT disease code system (ICD-10/ICD-O mapping)
"""
import re
from dataclasses import dataclass, field

import structlog

from app.knowledge.vocabulary import (
    Organ,
    UDAFVocabulary,
)

logger = structlog.get_logger("app.knowledge.udt_parser")


@dataclass
class ParsedAttribute:
    """Parsed diagnostic attribute."""
    category: str
    value: str
    raw_text: str
    confidence: float = 1.0
    organ: str | None = None


# DESIGN-004: structured measurement value
@dataclass
class MeasurementValue:
    """Structured measurement value."""
    value1: float
    value2: float | None = None
    unit: str = "cm"
    measurement_type: str | None = None

    def __str__(self) -> str:
        if self.value2 is not None:
            return f"{self.value1}x{self.value2} {self.unit}"
        return f"{self.value1} {self.unit}"


@dataclass
class UDTParseResult:
    """Result of UDT parsing."""
    raw_text: str
    attributes: list[ParsedAttribute] = field(default_factory=list)
    normalized_terms: list[str] = field(default_factory=list)
    organ: str | None = None
    unrecognized_terms: list[str] = field(default_factory=list)

    @property
    def coverage_rate(self) -> float:
        """Calculate terminology coverage rate."""
        total = len(self.attributes) + len(self.unrecognized_terms)
        if total == 0:
            return 1.0
        return len(self.attributes) / total


@dataclass
class UDTParserConfig:
    """Configuration for UDT Parser."""
    min_confidence: float = 0.5
    extract_measurements: bool = True
    extract_classifications: bool = True
    detect_organ: bool = True
    min_token_length: int = 2


_RADS_LEVEL_MAP = {
    "1": "1", "2": "2", "3": "3",
    "4A": "4A", "4a": "4A",
    "4B": "4B", "4b": "4B",
    "4C": "4C", "4c": "4C",
    "5": "5", "6": "6",
}

_LI_RADS_LEVEL_MAP = {
    "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
    "M": "M", "TR": "TR",
}

_ATTRIBUTE_CATEGORIES = [
    "echogenicity", "boundary", "morphology", "vascularity", "calcification",
    "heart_structure", "heart_condition", "heart_function",
    # M9 后方回声特征（UDAF 属性扩展）。派生测量（derived_measurement）
    # 不在此列——它由 bbox/像素间距计算得到，不从文本匹配。
    "posterior_acoustic",
]


class UDTParser:
    """
    Unified Diagnostic Taxonomy Parser.
    Parses and normalizes ultrasound diagnostic text.
    """

    # 器官关键词表（按优先级排序）。集中一处供 ``detect_organs`` /
    # ``_detect_organ`` 共用，避免两份表漂移。同义表达在原有基础上扩展：
    # 「甲亢/甲减」→ 甲状腺、「心律失常/心功能/左室」→ 心脏。
    ORGAN_KEYWORDS: tuple = (
        (Organ.THYROID, ('甲状腺', '甲亢', '甲减', 'thyroid')),
        (Organ.LIVER, ('肝脏', '肝右', '肝左', 'liver')),
        (Organ.HEART, ('心脏', '心功能', '心律失常', '左室', 'heart', 'cardiac')),
    )

    def __init__(self, vocabulary: UDAFVocabulary | None = None,
                 config: UDTParserConfig | None = None):
        self.vocab = vocabulary or UDAFVocabulary()
        self.config = config or UDTParserConfig()
        self._init_patterns()
        # NEW-003: pre-cache sorted synonyms (longest first) at init time
        self._sorted_synonyms: list[tuple[str, str]] = sorted(
            list(self.vocab.iter_synonym_mappings()),
            key=lambda x: -len(x[0])
        )

    def _init_patterns(self):
        """Initialize regex patterns for attribute extraction."""
        self._measurement_pattern = re.compile(
            r'(\d+(?:\.\d+)?)\s*(?:cm|mm)?\s*(?:\u00d7|x|\*)\s*(\d+(?:\.\d+)?)?\s*(?:cm|mm)?',
            re.IGNORECASE
        )
        self._tirads_pattern = re.compile(
            r'TI[-\s]?RADS[-\s]?(\d[ABC]?)', re.IGNORECASE
        )
        self._lirads_pattern = re.compile(
            r'(?:LI[-\s]?RADS|LR)[-\s]?(\d[MTR]?)', re.IGNORECASE
        )

    def parse(self, text: str | None, organ: str | None = None,
              _clean_text: str | None = None) -> UDTParseResult:
        """
        Parse diagnostic text into standardized attributes.

        Args:
            text: Raw diagnostic text
            organ: Optional organ hint for context
            _clean_text: Pre-processed text (internal use, avoids redundant preprocessing)

        Returns:
            UDTParseResult with parsed attributes
        """
        if not text or not text.strip():
            return UDTParseResult(raw_text=text or "", organ=organ)

        try:
            clean_text = _clean_text if _clean_text is not None else self._preprocess_text(text)

            if self.config.detect_organ and not organ:
                organ = self._detect_organ(clean_text)

            attributes = self._extract_all_attributes(clean_text, organ)
            normalized_terms = [attr.value for attr in attributes]

            recognized_spans = set(attr.raw_text for attr in attributes)
            unrecognized_terms = self._find_unrecognized(clean_text, recognized_spans)

            return UDTParseResult(
                raw_text=text,
                attributes=attributes,
                normalized_terms=normalized_terms,
                organ=organ,
                unrecognized_terms=unrecognized_terms,
            )
        except Exception as e:
            logger.error("UDT parse failed", error=str(e), text=text[:100])
            return UDTParseResult(raw_text=text, organ=organ)

    def _preprocess_text(self, text: str) -> str:
        """Preprocess text for parsing."""
        text = re.sub(r'\s+', ' ', text.strip())
        text = text.replace('\uff0c', ',').replace('\u3002', '.').replace('\uff1b', ';')
        return text

    def _detect_organ(self, text: str) -> Organ | None:
        """Detect organ from text（首个命中的器官，语义与原有实现一致）。"""
        organs = self.detect_organs(text)
        return organs[0] if organs else None

    def detect_organs(self, text: str) -> list[Organ]:
        """Detect **all** organs mentioned in the text（按优先级顺序）。

        与 ``_detect_organ`` 共用同一张关键词表。之所以需要复数版本：Adaptive
        RAG 动态路由（01-系统架构设计 §5.3）要判断"是否涉及跨器官关联"
        （示例「甲亢+心律失常」），单值检测无法表达。

        关键词表在原基础上扩展了同义/近义表达：规格示例「甲亢+心律失常」
        既无「甲状腺」也无「心脏」字面词，仅凭原关键词无法识别。
        """
        text_lower = (text or "").lower()
        found: list[Organ] = []
        for organ, keywords in self.ORGAN_KEYWORDS:
            if any(kw in text_lower for kw in keywords):
                found.append(organ)
        return found

    def _extract_all_attributes(self, text: str,
                                organ: str | None) -> list[ParsedAttribute]:
        """Extract all attributes from text.

        M9 后方回声（``posterior_acoustic``）先于其它类别匹配，并将其命中
        span 作为**抑制区间**传给后续类别：否则"后方回声增强"会在
        ``echogenicity`` 独立命中内部的"回声增强"，产出并非病灶自身属性的
        ``hyperechoic`` 假阳性（会污染后续分级评分）。

        当文本不含后方回声术语时，抑制区间为空，抽取顺序与结果与改造前
        完全一致。
        """
        attributes: list[ParsedAttribute] = []

        blocked_spans: list[tuple[int, int]] = []
        if "posterior_acoustic" in _ATTRIBUTE_CATEGORIES:
            attributes.extend(self._extract_by_category(
                text, "posterior_acoustic", organ, record_spans=blocked_spans))

        for category in _ATTRIBUTE_CATEGORIES:
            if category == "posterior_acoustic":
                # 已在上方优先匹配
                continue
            attributes.extend(self._extract_by_category(
                text, category, organ, blocked_spans=blocked_spans))

        if self.config.extract_classifications:
            attributes.extend(self._extract_classification(text))

        if self.config.extract_measurements:
            attributes.extend(self._extract_measurements(text, organ))

        return attributes

    def _extract_by_category(self, text: str, category: str,
                             organ: str | None,
                             blocked_spans: list[tuple[int, int]] | None = None,
                             record_spans: list[tuple[int, int]] | None = None,
                             ) -> list[ParsedAttribute]:
        """Extract attributes for a given category using Trie-based multi-pattern matching.

        Optimized from O(n*m) to O(T + S) complexity:
        - T = len(text), S = total characters in synonyms + terms
        - Uses a Trie to find all vocabulary matches in a single text scan.

        Args:
            blocked_spans: 其它类别的已匹配区间；与之重叠的命中被丢弃
                （M9：抑制"后方回声增强"内部的"回声增强"误匹配）。
            record_spans: 若提供，把本类别命中的区间写回该列表，供调用方
                作为后续类别的抑制区间。
        """
        category_terms = set(self.vocab.get_terms_by_category(category))
        attributes: list[ParsedAttribute] = []
        matched_spans: list[tuple[int, int]] = []

        trie: dict = {}
        for synonym, standard in self._sorted_synonyms:
            if standard not in category_terms:
                continue
            node = trie
            for ch in synonym:
                if ch not in node:
                    node[ch] = {}
                node = node[ch]
            if '*' not in node:
                node['*'] = (standard, synonym)

        for term in category_terms:
            if not term:
                continue
            node = trie
            for ch in term:
                if ch not in node:
                    node[ch] = {}
                node = node[ch]
            marker = (term, term)
            if '*' not in node or len(term) > len(node['*'][1]):
                node['*'] = marker

        i = 0
        while i < len(text):
            node = trie
            match_end = -1
            match_value = None
            match_raw = None
            j = i
            while j < len(text) and text[j] in node:
                node = node[text[j]]
                if '*' in node:
                    match_end = j + 1
                    match_value = node['*'][0]
                    match_raw = node['*'][1]
                j += 1

            if match_end != -1:
                span = (i, match_end)
                blocked = bool(blocked_spans) and self._overlaps(span, blocked_spans)
                if not blocked and not self._overlaps(span, matched_spans):
                    matched_spans.append(span)
                    if record_spans is not None:
                        record_spans.append(span)
                    attributes.append(ParsedAttribute(
                        category=category,
                        value=match_value,
                        raw_text=match_raw,
                        organ=organ,
                    ))
                i = match_end
            else:
                i += 1

        return attributes

    @staticmethod
    def _overlaps(span: tuple[int, int], matched: list[tuple[int, int]]) -> bool:
        """Check if a span overlaps with any already-matched span."""
        s_start, s_end = span
        return any(s_start < m_end and s_end > m_start for m_start, m_end in matched)

    def _extract_classification(self, text: str) -> list[ParsedAttribute]:
        """Extract classification attributes (TI-RADS, LI-RADS)."""
        attributes: list[ParsedAttribute] = []

        tirads_match = self._tirads_pattern.search(text)
        if tirads_match:
            level = tirads_match.group(1)
            mapped = _RADS_LEVEL_MAP.get(level)
            if mapped:
                attributes.append(ParsedAttribute(
                    category='ti_rads',
                    value=f'TI-RADS {mapped}',
                    raw_text=tirads_match.group(0),
                    organ=Organ.THYROID,
                ))

        lirads_match = self._lirads_pattern.search(text)
        if lirads_match:
            level = lirads_match.group(1).upper()
            mapped = _LI_RADS_LEVEL_MAP.get(level)
            if mapped:
                label = f'LI-RADS {mapped}'
                attributes.append(ParsedAttribute(
                    category='li_rads',
                    value=label,
                    raw_text=lirads_match.group(0),
                    organ=Organ.LIVER,
                ))

        return attributes

    # NEW-001: MeasurementValue integrated into extraction
    def _extract_measurements(self, text: str, organ: str | None) -> list[ParsedAttribute]:
        """Extract measurement attributes with structured MeasurementValue."""
        attributes: list[ParsedAttribute] = []
        for match in self._measurement_pattern.finditer(text):
            raw = match.group(0)
            try:
                v1 = float(match.group(1))
            except (ValueError, TypeError):
                continue
            v2_str = match.group(2)
            v2 = float(v2_str) if v2_str else None
            mv = MeasurementValue(value1=v1, value2=v2)
            attributes.append(ParsedAttribute(
                category='measurement',
                value=str(mv),
                raw_text=raw,
                organ=organ,
            ))
        return attributes

    def _find_unrecognized(self, text: str,
                           recognized_raw: set[str]) -> list[str]:
        """Find unrecognized terms from text segments."""
        segments = re.split(r'[,.;\s\uff0c\u3002\uff1b\uff08\uff09()（）]+', text)
        unrecognized = []
        for seg in segments:
            seg = seg.strip()
            if len(seg) < self.config.min_token_length:
                continue
            if seg in recognized_raw:
                continue
            normalized = self.vocab.normalize(seg)
            if not normalized and not self.vocab.has_synonym(seg):
                unrecognized.append(seg)
        return unrecognized

    # NEW-004 + BUG-007 residual fix: use clean_text positions for replacement
    # instead of searching in raw report (which may have different positions)
    def normalize_report(self, report: str, organ: str | None = None) -> str:
        """
        Normalize an entire diagnostic report.

        Args:
            report: Raw diagnostic report
            organ: Optional organ hint

        Returns:
            Normalized report text
        """
        clean_text = self._preprocess_text(report)
        result = self.parse(report, organ, _clean_text=clean_text)

        # Build replacements using clean_text positions
        replacements: list[tuple[int, int, str]] = []
        for attr in result.attributes:
            start = 0
            while True:
                idx = clean_text.find(attr.raw_text, start)
                if idx == -1:
                    break
                end = idx + len(attr.raw_text)
                replacements.append((idx, end, attr.value))
                start = idx + 1

        # Sort by position, then deduplicate overlapping ranges
        replacements.sort(key=lambda r: (r[0], -r[1]))

        # Build normalized text from clean_text using position-based replacement
        normalized_parts: list[str] = []
        prev_end = 0
        for start, end, replacement in replacements:
            if start < prev_end:
                continue
            normalized_parts.append(clean_text[prev_end:start])
            normalized_parts.append(replacement)
            prev_end = end
        normalized_parts.append(clean_text[prev_end:])

        return "".join(normalized_parts)
