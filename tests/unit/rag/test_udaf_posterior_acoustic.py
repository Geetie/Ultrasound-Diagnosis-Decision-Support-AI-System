"""M9 后方回声特征（UDAF 属性扩展）与 M3 派生测量术语单测。

覆盖三件事：
1. 新增类别的术语注册正确、且**没有夺走**既有类别的标准术语键；
2. 解析器对自然表述（"后方回声增强"）能抽到后方回声属性，且**不产生**
   病灶属性的假阳性（"回声增强" 曾被 echogenicity 在同一位置独立命中）；
3. 不含后方回声的文本，抽取结果与顺序与扩展前一致（无回归）。
"""
import pytest

from app.knowledge.udt_parser import UDTParser
from app.knowledge.vocabulary import (
    DERIVED_MEASUREMENT_CATEGORY,
    POSTERIOR_ACOUSTIC_ENUM_MAP,
    UDAFVocabulary,
    posterior_acoustic_value,
)

pytestmark = [pytest.mark.track_rag, pytest.mark.track_data]


@pytest.fixture
def vocab() -> UDAFVocabulary:
    return UDAFVocabulary()


@pytest.fixture
def parser(vocab: UDAFVocabulary) -> UDTParser:
    return UDTParser(vocabulary=vocab)


def _attrs(result) -> list[tuple[str, str]]:
    return [(a.category, a.value) for a in result.attributes]


class TestVocabularyRegistration:
    def test_posterior_acoustic_category_registered(self, vocab):
        terms = set(vocab.get_terms_by_category("posterior_acoustic"))
        assert terms == set(POSTERIOR_ACOUSTIC_ENUM_MAP.keys())

    def test_derived_measurement_category_registered(self, vocab):
        terms = set(vocab.get_terms_by_category(DERIVED_MEASUREMENT_CATEGORY))
        assert terms == {
            "aspect_ratio",
            "volume",
            "distance_to_capsule",
            "distance_to_organ_edge",
            "distance_to_skin",
        }

    def test_no_existing_standard_terms_stolen(self, vocab):
        """新增类别不得侵占既有标准术语键（``_vocab`` 是全局键空间）。"""
        # 既有实现中 none→calcification、mixed→vascularity、irregular→morphology
        # 都是"后注册者覆盖先注册者"的既成事实，扩展后必须保持原样。
        assert "none" in vocab.get_terms_by_category("calcification")
        assert "mixed" in vocab.get_terms_by_category("vascularity")
        assert "irregular" in vocab.get_terms_by_category("morphology")
        assert vocab.get_mapping("none").category == "calcification"
        assert vocab.get_mapping("mixed").category == "vascularity"

    def test_posterior_terms_are_namespaced(self, vocab):
        """后方回声标准术语必须带前缀，避免与短名类别冲突。"""
        for term in POSTERIOR_ACOUSTIC_ENUM_MAP:
            assert term.startswith("posterior_")
            assert vocab.get_mapping(term).category == "posterior_acoustic"

    def test_synonyms_resolve_to_standard_terms(self, vocab):
        assert vocab.normalize("后方回声增强") == "posterior_enhancement"
        assert vocab.normalize("后方声影") == "posterior_shadowing"
        assert vocab.normalize("后方回声衰减") == "posterior_attenuation"


class TestPosteriorAcousticValueMapping:
    @pytest.mark.parametrize(
        "standard,expected",
        [
            ("posterior_enhancement", "enhancement"),
            ("posterior_attenuation", "attenuation"),
            ("posterior_shadowing", "shadowing"),
            ("posterior_none", "none"),
            ("posterior_mixed", "mixed"),
        ],
    )
    def test_maps_to_contract_enum(self, standard, expected):
        assert posterior_acoustic_value(standard) == expected

    @pytest.mark.parametrize("value", [None, "", "hyperechoic", "unknown_term"])
    def test_non_posterior_returns_none(self, value):
        assert posterior_acoustic_value(value) is None


class TestParserExtraction:
    def test_extracts_posterior_enhancement(self, parser):
        result = parser.parse("甲状腺右叶低回声结节，后方回声增强，边界不清", organ="thyroid")
        attrs = _attrs(result)
        assert ("posterior_acoustic", "posterior_enhancement") in attrs
        # 关键：不得因"后方回声增强"内部的"回声增强"而多出病灶 hyperechoic
        assert ("echogenicity", "hyperechoic") not in attrs
        # 结节自身的低回声仍应保留
        assert ("echogenicity", "hypoechoic") in attrs

    def test_extracts_posterior_shadowing(self, parser):
        result = parser.parse("结节后方声影，大小 1.2x0.8 cm", organ="thyroid")
        assert ("posterior_acoustic", "posterior_shadowing") in _attrs(result)

    def test_extracts_posterior_none_and_keeps_calcification(self, parser):
        result = parser.parse("后方回声无异常，无钙化，边界清晰", organ="thyroid")
        attrs = _attrs(result)
        assert ("posterior_acoustic", "posterior_none") in attrs
        assert ("calcification", "none") in attrs
        assert ("boundary", "clear") in attrs

    def test_extracts_posterior_attenuation_with_other_findings(self, parser):
        result = parser.parse("后方回声衰减，内见微钙化，形态不规则", organ="thyroid")
        attrs = _attrs(result)
        assert ("posterior_acoustic", "posterior_attenuation") in attrs
        assert ("calcification", "micro") in attrs
        assert ("morphology", "irregular") in attrs

    def test_text_without_posterior_is_unchanged(self, parser):
        """无后方回声文本：抽取顺序与内容保持扩展前基线（无回归）。"""
        result = parser.parse(
            "甲状腺右叶低回声结节，边界不清，形态不规则，内部血流信号，"
            "伴微钙化，TI-RADS 4A，大小 1.2x0.8 cm",
            organ="thyroid",
        )
        assert _attrs(result) == [
            ("echogenicity", "hypoechoic"),
            ("boundary", "ill-defined"),
            ("morphology", "irregular"),
            ("vascularity", "internal"),
            ("calcification", "micro"),
            ("ti_rads", "TI-RADS 4A"),
            ("measurement", "1.2x0.8 cm"),
        ]
