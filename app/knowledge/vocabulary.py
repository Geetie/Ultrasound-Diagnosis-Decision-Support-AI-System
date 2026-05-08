"""
UDAF (Unified Diagnostic Attribute Framework) Vocabulary
Provides standardized terminology mapping for ultrasound diagnosis.

TODO(Phase0A): DESIGN-001 - Implement UHAT 4-level hierarchy (organ->system->feature->term)
TODO(Phase0A): DESIGN-002 - Add term review/approval workflow
TODO(Phase0A): DESIGN-003 - Add unmapped term review queue for continuous vocabulary improvement
"""
from enum import Enum
from typing import Dict, List, Optional, Set
from dataclasses import dataclass


@dataclass
class TermMapping:
    """Term mapping with synonyms and related terms."""
    standard: str
    synonyms: List[str]
    related_terms: List[str]
    organ: Optional[str] = None
    category: Optional[str] = None


class Organ(str, Enum):
    """Organ enumeration."""
    THYROID = "thyroid"
    LIVER = "liver"
    HEART = "heart"


class Echogenicity(str, Enum):
    """Echogenicity enumeration."""
    HYPERECHOIC = "hyperechoic"
    HYPOECHOIC = "hypoechoic"
    ISOECHOIC = "isoechoic"
    MIXED = "mixed"
    ANECHOIC = "anechoic"


class Boundary(str, Enum):
    """Boundary enumeration."""
    CLEAR = "clear"
    ILL_DEFINED = "ill-defined"
    IRREGULAR = "irregular"
    LOBULATED = "lobulated"


class Morphology(str, Enum):
    """Morphology enumeration."""
    ROUND = "round"
    OVAL = "oval"
    LOBULATED = "lobulated"
    IRREGULAR = "irregular"


class Vascularity(str, Enum):
    """Vascularity enumeration."""
    ABSENT = "absent"
    PERIPHERAL = "peripheral"
    INTERNAL = "internal"
    MIXED = "mixed"


class Calcification(str, Enum):
    """Calcification enumeration."""
    NONE = "none"
    MICRO = "micro"
    COARSE = "coarse"
    RIM = "rim"


class MeasurementType(str, Enum):
    """Measurement type enumeration."""
    LONGITUDINAL = "longitudinal"
    TRANSVERSE = "transverse"
    ANTEROPOSTERIOR = "anteroposterior"


class TI_RADS(str, Enum):
    """TI-RADS enumeration."""
    TI_RADS_1 = "TI-RADS 1"
    TI_RADS_2 = "TI-RADS 2"
    TI_RADS_3 = "TI-RADS 3"
    TI_RADS_4A = "TI-RADS 4A"
    TI_RADS_4B = "TI-RADS 4B"
    TI_RADS_4C = "TI-RADS 4C"
    TI_RADS_5 = "TI-RADS 5"
    TI_RADS_6 = "TI-RADS 6"


class LI_RADS(str, Enum):
    """LI-RADS enumeration."""
    LI_RADS_1 = "LI-RADS 1"
    LI_RADS_2 = "LI-RADS 2"
    LI_RADS_3 = "LI-RADS 3"
    LI_RADS_4 = "LI-RADS 4"
    LI_RADS_5 = "LI-RADS 5"
    LI_RADS_M = "LI-RADS M"
    LI_RADS_TR = "LI-RADS TR"


class HeartStructure(str, Enum):
    """心脏结构枚举."""
    LEFT_ATRIUM = "left_atrium"
    LEFT_VENTRICLE = "left_ventricle"
    RIGHT_ATRIUM = "right_atrium"
    RIGHT_VENTRICLE = "right_ventricle"
    MITRAL_VALVE = "mitral_valve"
    TRICUSPID_VALVE = "tricuspid_valve"
    AORTIC_VALVE = "aortic_valve"
    PULMONARY_VALVE = "pulmonary_valve"
    INTERVENTRICULAR_SEPTUM = "interventricular_septum"
    LEFT_VENTRICLE_WALL = "left_ventricle_wall"
    PERICARDIUM = "pericardium"
    AORTA = "aorta"


class HeartCondition(str, Enum):
    """心脏状况枚举."""
    NORMAL = "normal"
    ENLARGED = "enlarged"
    THICKENED = "thickened"
    MILD_REGURGITATION = "mild_regurgitation"
    MODERATE_REGURGITATION = "moderate_regurgitation"
    SEVERE_REGURGITATION = "severe_regurgitation"
    STENOSIS = "stenosis"
    HYPOKINESIS = "hypokinesis"
    NO_ABNORMALITY = "no_abnormality"
    REDUCED_SYSTOLIC_FUNCTION = "reduced_systolic_function"
    REDUCED_DIASTOLIC_FUNCTION = "reduced_diastolic_function"
    SMALL_EFFUSION = "small_effusion"
    NO_EFFUSION = "no_effusion"


class HeartFunctionGrade(str, Enum):
    """心功能分级枚举."""
    EF_NORMAL = "ef_normal"  # EF > 55%
    EF_MILD_REDUCED = "ef_mild_reduced"  # 40-55%
    EF_MODERATE_REDUCED = "ef_moderate_reduced"  # 30-40%
    EF_SEVERE_REDUCED = "ef_severe_reduced"  # < 30%


class AnatomicalLocation(str, Enum):
    """Anatomical location enumeration for thyroid."""
    RIGHT_LOBE_UPPER = "right_lobe_upper"
    RIGHT_LOBE_MIDDLE = "right_lobe_middle"
    RIGHT_LOBE_LOWER = "right_lobe_lower"
    LEFT_LOBE_UPPER = "left_lobe_upper"
    LEFT_LOBE_MIDDLE = "left_lobe_middle"
    LEFT_LOBE_LOWER = "left_lobe_lower"
    ISTHMUS = "isthmus"


class UDAFVocabulary:
    """
    Unified Diagnostic Attribute Framework Vocabulary.
    Provides standardized terminology mapping for ultrasound diagnosis.
    """

    def __init__(self):
        self._vocab: Dict[str, TermMapping] = {}
        self._reverse_lookup: Dict[str, str] = {}
        self._initialize_vocabulary()

    def _initialize_vocabulary(self):
        """Initialize the UDAF vocabulary with standard terms and mappings."""
        self._init_echogenicity()
        self._init_boundary()
        self._init_morphology()
        self._init_vascularity()
        self._init_calcification()
        self._init_anatomical_locations()
        self._init_tirads()
        self._init_lirads()
        self._init_heart_structures()
        self._init_heart_conditions()
        self._init_heart_function()

    def _init_echogenicity(self):
        terms = [
            ("hyperechoic", ["高回声", "强回声", "回声增强", "高回声区", "回声稍强"], []),
            ("hypoechoic", ["低回声", "回声减低", "低回声区", "回声稍低"], []),
            ("isoechoic", ["等回声", "等回声区"], []),
            ("mixed", ["混合回声", "不均质回声", "回声不均", "混合回声区"], []),
            ("anechoic", ["无回声", "液性暗区", "无回声区"], []),
        ]
        for standard, synonyms, related in terms:
            self._add_mapping(standard, synonyms, related, category="echogenicity")

    def _init_boundary(self):
        # BUG-002 fix: "形态不规则" removed from boundary synonyms,
        # it belongs only to morphology category
        terms = [
            ("clear", ["边界清", "边界清晰", "境界清", "境界清晰", "边界清楚", "边界尚清"], []),
            ("ill-defined", ["边界不清", "境界不清", "边界欠清", "边界模糊"], []),
            ("irregular", ["边界不规则"], []),
            ("lobulated", ["分叶状", "边界分叶"], []),
        ]
        for standard, synonyms, related in terms:
            self._add_mapping(standard, synonyms, related, category="boundary")

    def _init_morphology(self):
        # BUG-012 note: "形态规则" maps to "oval" per ACR TI-RADS:
        # oval/round are benign indicators; "oval" is the more common
        # standard term for "规则" shape in thyroid nodule reporting
        terms = [
            ("round", ["圆形", "类圆形", "近圆形"], []),
            ("oval", ["椭圆形", "卵圆型", "形态规则", "规则", "类椭圆形"], []),
            ("lobulated", ["分叶状", "呈分叶状"], []),
            ("irregular", ["不规则形", "形态不规则", "不规则", "形态欠规则"], []),
        ]
        for standard, synonyms, related in terms:
            self._add_mapping(standard, synonyms, related, category="morphology")

    def _init_vascularity(self):
        terms = [
            ("absent", ["无血流", "未见明显血流", "未见血流", "血流不丰富", "血流信号不丰富", "未见血流信号"], []),
            ("peripheral", ["周边血流", "边缘血流", "环状血流", "环绕血流"], []),
            ("internal", ["内部血流", "中央血流", "内部见血流", "中心血流"], []),
            ("mixed", ["混合血流", "周边及内部血流", "血流丰富", "丰富血流", "血流信号丰富", "少许血流", "少量血流"], []),
        ]
        for standard, synonyms, related in terms:
            self._add_mapping(standard, synonyms, related, category="vascularity")

    def _init_calcification(self):
        terms = [
            ("none", ["无钙化", "未见钙化"], []),
            ("micro", ["微钙化", "细小钙化", "针尖样钙化", "点状钙化"], []),
            ("coarse", ["粗大钙化", "粗钙化", "斑片状钙化"], []),
            ("rim", ["环状钙化", "环形钙化", "蛋壳样钙化"], []),
        ]
        for standard, synonyms, related in terms:
            self._add_mapping(standard, synonyms, related, category="calcification")

    def _init_anatomical_locations(self):
        location_terms = [
            ("right_lobe_upper", ["右叶上极", "右叶上部"], [], Organ.THYROID),
            ("right_lobe_middle", ["右叶中极", "右叶中部"], [], Organ.THYROID),
            ("right_lobe_lower", ["右叶下极", "右叶下部"], [], Organ.THYROID),
            ("left_lobe_upper", ["左叶上极", "左叶上部"], [], Organ.THYROID),
            ("left_lobe_middle", ["左叶中极", "左叶中部"], [], Organ.THYROID),
            ("left_lobe_lower", ["左叶下极", "左叶下部"], [], Organ.THYROID),
            ("isthmus", ["峡部", "甲状腺峡部"], [], Organ.THYROID),
        ]
        for standard, synonyms, related, organ in location_terms:
            self._add_mapping(standard, synonyms, related, organ=organ, category="anatomical_location")

    def _init_tirads(self):
        tirads_terms = [
            ("TI-RADS 1", ["TI-RADS1", "TI RADS 1", "1类", "TI-RADS1类"], [], Organ.THYROID),
            ("TI-RADS 2", ["TI-RADS2", "TI RADS 2", "2类", "TI-RADS2类"], [], Organ.THYROID),
            ("TI-RADS 3", ["TI-RADS3", "TI RADS 3", "3类", "TI-RADS3类"], [], Organ.THYROID),
            ("TI-RADS 4A", ["TI-RADS4A", "TI RADS 4A", "4A类", "TI-RADS4A类"], [], Organ.THYROID),
            ("TI-RADS 4B", ["TI-RADS4B", "TI RADS 4B", "4B类", "TI-RADS4B类"], [], Organ.THYROID),
            ("TI-RADS 4C", ["TI-RADS4C", "TI RADS 4C", "4C类", "TI-RADS4C类"], [], Organ.THYROID),
            ("TI-RADS 5", ["TI-RADS5", "TI RADS 5", "5类", "TI-RADS5类"], [], Organ.THYROID),
            ("TI-RADS 6", ["TI-RADS6", "TI RADS 6", "6类", "TI-RADS6类"], [], Organ.THYROID),
        ]
        for standard, synonyms, related, organ in tirads_terms:
            self._add_mapping(standard, synonyms, related, organ=organ, category="ti_rads")

    def _init_lirads(self):
        lirads_terms = [
            ("LI-RADS 1", ["LI-RADS1", "LI RADS 1", "LR-1", "LR1"], [], Organ.LIVER),
            ("LI-RADS 2", ["LI-RADS2", "LI RADS 2", "LR-2", "LR2"], [], Organ.LIVER),
            ("LI-RADS 3", ["LI-RADS3", "LI RADS 3", "LR-3", "LR3"], [], Organ.LIVER),
            ("LI-RADS 4", ["LI-RADS4", "LI RADS 4", "LR-4", "LR4"], [], Organ.LIVER),
            ("LI-RADS 5", ["LI-RADS5", "LI RADS 5", "LR-5", "LR5"], [], Organ.LIVER),
            ("LI-RADS M", ["LI-RADS-M", "LI RADS M", "LR-M", "LRM"], [], Organ.LIVER),
            ("LI-RADS TR", ["LI-RADS-TR", "LI RADS TR", "LR-TR", "LRTR"], [], Organ.LIVER),
        ]
        for standard, synonyms, related, organ in lirads_terms:
            self._add_mapping(standard, synonyms, related, organ=organ, category="li_rads")

    def _init_heart_structures(self):
        """初始化心脏结构术语."""
        terms = [
            ("left_atrium", ["左房", "左心房"], [], Organ.HEART),
            ("left_ventricle", ["左室", "左心室"], [], Organ.HEART),
            ("right_atrium", ["右房", "右心房"], [], Organ.HEART),
            ("right_ventricle", ["右室", "右心室"], [], Organ.HEART),
            ("mitral_valve", ["二尖瓣", "二尖瓣瓣口"], [], Organ.HEART),
            ("tricuspid_valve", ["三尖瓣", "三尖瓣瓣口"], [], Organ.HEART),
            ("aortic_valve", ["主动脉瓣"], [], Organ.HEART),
            ("pulmonary_valve", ["肺动脉瓣"], [], Organ.HEART),
            ("interventricular_septum", ["室间隔"], [], Organ.HEART),
            ("left_ventricle_wall", ["左室壁", "左室后壁", "左室侧壁"], [], Organ.HEART),
            ("pericardium", ["心包", "心包腔"], [], Organ.HEART),
            ("aorta", ["主动脉", "升主动脉", "降主动脉"], [], Organ.HEART),
        ]
        for standard, synonyms, related, organ in terms:
            self._add_mapping(standard, synonyms, related, organ=organ, category="heart_structure")

    def _init_heart_conditions(self):
        """初始化心脏状况术语."""
        terms = [
            ("normal", ["未见明显异常", "未见异常"], [], Organ.HEART),
            ("enlarged", ["增大", "扩大"], [], Organ.HEART),
            ("thickened", ["增厚", "增厚明显"], [], Organ.HEART),
            ("mild_regurgitation", ["轻度反流", "少量反流"], [], Organ.HEART),
            ("moderate_regurgitation", ["中度反流", "中量反流"], [], Organ.HEART),
            ("severe_regurgitation", ["重度反流", "大量反流"], [], Organ.HEART),
            ("stenosis", ["狭窄", "瓣口狭窄"], [], Organ.HEART),
            ("hypokinesis", ["运动减低", "运动减弱"], [], Organ.HEART),
            ("no_abnormality", ["结构未见明显异常", "未见器质性病变"], [], Organ.HEART),
            ("reduced_systolic_function", ["收缩功能减低", "收缩功能下降"], [], Organ.HEART),
            ("reduced_diastolic_function", ["舒张功能减低", "舒张功能下降"], [], Organ.HEART),
            ("small_effusion", ["少量积液", "微量积液"], [], Organ.HEART),
            ("no_effusion", ["未见积液", "心包腔未见积液"], [], Organ.HEART),
        ]
        for standard, synonyms, related, organ in terms:
            self._add_mapping(standard, synonyms, related, organ=organ, category="heart_condition")

    def _init_heart_function(self):
        """初始化心功能分级术语."""
        terms = [
            ("ef_normal", ["EF正常", "左室射血分数正常", "EF>55%"], [], Organ.HEART),
            ("ef_mild_reduced", ["EF轻度减低", "EF40-55%"], [], Organ.HEART),
            ("ef_moderate_reduced", ["EF中度减低", "EF30-40%"], [], Organ.HEART),
            ("ef_severe_reduced", ["EF重度减低", "EF<30%"], [], Organ.HEART),
        ]
        for standard, synonyms, related, organ in terms:
            self._add_mapping(standard, synonyms, related, organ=organ, category="heart_function")

    def _add_mapping(self, standard: str, synonyms: List[str], related_terms: List[str],
                     organ: Optional[str] = None, category: Optional[str] = None):
        """Add a term mapping to the vocabulary."""
        mapping = TermMapping(
            standard=standard,
            synonyms=synonyms,
            related_terms=related_terms,
            organ=organ,
            category=category
        )
        self._vocab[standard] = mapping
        for synonym in synonyms:
            self._reverse_lookup[synonym] = standard

    # BUG-001 fix: type annotation changed from str to Optional[str]
    def normalize(self, term: Optional[str]) -> Optional[str]:
        """
        Normalize a term to its standard form.

        Args:
            term: Input term to normalize (None returns None)

        Returns:
            Standardized term or None if not found
        """
        if not term:
            return None
        term_clean = term.strip()
        if term_clean in self._vocab:
            return term_clean
        return self._reverse_lookup.get(term_clean)

    def get_mapping(self, standard_term: str) -> Optional[TermMapping]:
        """
        Get the full mapping for a standard term.

        Args:
            standard_term: Standard term to look up

        Returns:
            TermMapping object or None if not found
        """
        return self._vocab.get(standard_term)

    def get_synonyms(self, term: str) -> List[str]:
        """
        Get all synonyms for a term (standard or synonym).

        Args:
            term: Input term

        Returns:
            List of synonyms
        """
        normalized = self.normalize(term)
        if not normalized:
            return []
        mapping = self._vocab.get(normalized)
        return mapping.synonyms if mapping else []

    def get_terms_by_category(self, category: str) -> List[str]:
        """
        Get all standard terms in a category.

        Args:
            category: Category name

        Returns:
            List of standard terms
        """
        return [
            term for term, mapping in self._vocab.items()
            if mapping.category == category
        ]

    # BUG-003 fix: handle both Organ enum and plain string comparison
    def get_terms_by_organ(self, organ: str) -> List[str]:
        """
        Get all standard terms for an organ.

        Args:
            organ: Organ name (Organ enum or string value)

        Returns:
            List of standard terms
        """
        organ_value = organ.value if isinstance(organ, Enum) else organ
        return [
            term for term, mapping in self._vocab.items()
            if mapping.organ == organ_value
        ]

    def coverage_rate(self, terms: List[str]) -> float:
        """
        Calculate terminology mapping coverage rate.

        Args:
            terms: List of terms to check

        Returns:
            Coverage rate (0.0 to 1.0)
        """
        if not terms:
            return 1.0
        normalized_count = sum(1 for term in terms if self.normalize(term))
        return normalized_count / len(terms)

    @property
    def all_standard_terms(self) -> Set[str]:
        """Get all standard terms in the vocabulary."""
        return set(self._vocab.keys())

    # STYLE-004 fix: public API for synonym lookup (replaces _reverse_lookup access)
    def has_synonym(self, text: str) -> bool:
        """
        Check if any synonym exists as a substring in the given text.

        Args:
            text: Text to search within

        Returns:
            True if any synonym is found in the text
        """
        for synonym in self._reverse_lookup:
            if synonym in text:
                return True
        return False

    # STYLE-004 fix: public API for iterating all synonyms
    def get_all_synonyms(self) -> Set[str]:
        """
        Get all synonym strings in the vocabulary.

        Returns:
            Set of all synonym strings
        """
        return set(self._reverse_lookup.keys())

    # STYLE-004 fix: public API for synonym-to-standard iteration
    def iter_synonym_mappings(self):
        """
        Iterate over all synonym-to-standard-term mappings.

        Yields:
            Tuples of (synonym, standard_term)
        """
        yield from self._reverse_lookup.items()
