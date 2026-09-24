"""M4 鉴别诊断清单解析单测。"""
import pytest

from app.knowledge.differential import (
    DEFAULT_MAX_CANDIDATES,
    differential_summary,
    parse_differential_diagnoses,
)

pytestmark = [pytest.mark.track_rag, pytest.mark.track_data]

STRUCTURED = """分级: TR4
置信度: 0.78
鉴别诊断:
1. 乳头状癌 | 0.60 | 支持: 微钙化、低回声 | 排除: 边界清晰
2. 结节性甲状腺肿 | 0.30 | 支持: 混合回声 | 排除: 微钙化
3. 亚急性甲状腺炎 | — | 支持: 片状低回声 | 排除: 无痛性结节
"""


class TestStructuredParsing:
    def test_parses_ranked_items(self):
        items = parse_differential_diagnoses(STRUCTURED)

        assert [item.rank for item in items] == [1, 2, 3]
        assert items[0].disease_name == '乳头状癌'
        assert items[0].confidence == 0.6
        assert items[0].supporting_evidence == ['微钙化', '低回声']
        assert items[0].excluding_evidence == ['边界清晰']

    def test_placeholder_confidence_is_none(self):
        """模型写"—"时保持 None：不反推数值（否则会制造看似精确的概率）。"""
        items = parse_differential_diagnoses(STRUCTURED)
        assert items[2].confidence is None

    def test_percentage_confidence_normalised(self):
        items = parse_differential_diagnoses(
            "鉴别诊断:\n1. 乳头状癌 | 60 | 支持: 微钙化\n"
        )
        assert items[0].confidence == 0.6

    def test_empty_evidence_sections(self):
        items = parse_differential_diagnoses("鉴别诊断:\n1. 结节性甲状腺肿 | 0.3\n")
        assert items[0].supporting_evidence == []
        assert items[0].excluding_evidence == []

    def test_full_width_separators_supported(self):
        items = parse_differential_diagnoses(
            "鉴别诊断:\n1. 乳头状癌 ｜ 0.6 ｜ 支持：微钙化 ｜ 排除：边界清晰\n"
        )
        assert items[0].disease_name == '乳头状癌'
        assert items[0].confidence == 0.6
        assert items[0].supporting_evidence == ['微钙化']

    def test_udt_code_extracted_from_name(self):
        items = parse_differential_diagnoses(
            "鉴别诊断:\n1. 乳头状癌 UDT-THYROID-PTC | 0.6\n"
        )
        assert items[0].disease_code == 'UDT-THYROID-PTC'
        assert items[0].disease_name == '乳头状癌'

    def test_sorted_by_rank(self):
        items = parse_differential_diagnoses(
            "鉴别诊断:\n3. 丙 | 0.1\n1. 甲 | 0.7\n2. 乙 | 0.4\n"
        )
        assert [item.disease_name for item in items] == ['甲', '乙', '丙']

    def test_deduplicates_case_insensitively(self):
        items = parse_differential_diagnoses(
            "鉴别诊断:\n1. Papillary | 0.6\n2. papillary | 0.3\n"
        )
        assert len(items) == 1

    def test_caps_candidate_count(self):
        lines = "\n".join(f"{i}. 病种{i} | 0.1" for i in range(1, 9))
        items = parse_differential_diagnoses(f"鉴别诊断:\n{lines}\n")
        assert len(items) == DEFAULT_MAX_CANDIDATES

    def test_ignores_non_list_lines(self):
        """像 "分级: TR4" 这样的行不能被误判为候选病种。"""
        items = parse_differential_diagnoses(
            "分级: TR4\n置信度: 0.78\n1. 乳头状癌 | 0.6\n"
        )
        assert len(items) == 1
        assert items[0].disease_name == '乳头状癌'


class TestLenientParsing:
    def test_comma_separated_list(self):
        items = parse_differential_diagnoses("鉴别诊断：结节性甲状腺肿、乳头状癌、亚急性甲状腺炎")

        assert [item.disease_name for item in items] == [
            '结节性甲状腺肿',
            '乳头状癌',
            '亚急性甲状腺炎',
        ]
        # 宽松解析拿不到可能性与依据
        assert all(item.confidence is None for item in items)
        assert all(item.supporting_evidence == [] for item in items)

    def test_structured_format_preferred(self):
        text = "鉴别诊断：甲、乙\n1. 乳头状癌 | 0.6\n"
        items = parse_differential_diagnoses(text)
        assert [item.disease_name for item in items] == ['乳头状癌']


class TestNoMatch:
    @pytest.mark.parametrize(
        "text",
        [None, "", "   ", "分级: TR4\n置信度: 0.78", "未见明显异常"],
    )
    def test_returns_empty(self, text):
        assert parse_differential_diagnoses(text) == []

    def test_overlong_candidate_rejected_in_lenient_mode(self):
        long_name = "很长的描述" * 20
        items = parse_differential_diagnoses(f"鉴别诊断：{long_name}")
        assert items == []


class TestSummary:
    def test_summary_joins_names(self):
        items = parse_differential_diagnoses(STRUCTURED)
        assert differential_summary(items) == '乳头状癌、结节性甲状腺肿、亚急性甲状腺炎'

    def test_summary_of_empty(self):
        assert differential_summary([]) == ''
