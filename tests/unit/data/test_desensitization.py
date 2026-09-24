"""Unit tests for app.knowledge.desensitization (T-GA-KB-DESENS-01).

P0-RAG-03: Patient desensitization at index time.

Covers:
- DesensitizationRuleSet loading & application
- Desensitizer heterogeneous redundancy (2 rule files)
- Fallback patterns (phone, ID card, email) when YAML is missing
- source_kind=guidelines → skip
- source_kind=other → apply
- Disagreement detection (only in file A vs only in file B)
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

pytestmark = [pytest.mark.track_knowledge]

from app.knowledge.desensitization import (
    DesensitizationRuleSet,
    Desensitizer,
    DesensitizeResult,
    PII_LABEL_EMAIL,
    PII_LABEL_ID_CARD,
    PII_LABEL_PHONE,
)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


@pytest.fixture
def safety_yaml(tmp_path: Path) -> Path:
    """YAML with id_card + phone rules (id_card first to avoid phone
    eating the head of an 18-digit ID card)."""
    p = tmp_path / "safety.yaml"
    p.write_text(
        dedent(
            """\
            rules:
              - name: "id_card_18"
                pattern: "(?<!\\\\d)\\\\d{17}[\\\\dXx](?!\\\\d)"
                label: "[REDACTED:ID_CARD]"
              - name: "phone_11"
                pattern: "(?<!\\\\d)1[3-9]\\\\d{9}(?!\\\\d)"
                label: "[REDACTED:PHONE]"
            """
        ),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def default_yaml(tmp_path: Path) -> Path:
    """YAML with only email rule (intentionally narrower)."""
    p = tmp_path / "default.yaml"
    p.write_text(
        dedent(
            """\
            rules:
              - name: "email"
                pattern: "\\\\b[\\\\w.+-]+@[\\\\w-]+\\\\.[\\\\w.-]+\\\\b"
                label: "[REDACTED:EMAIL]"
            """
        ),
        encoding="utf-8",
    )
    return p


# ----------------------------------------------------------------------
# DesensitizationRuleSet
# ----------------------------------------------------------------------


class TestDesensitizationRuleSet:
    def test_load_missing_file_does_not_raise(self, tmp_path: Path):
        p = tmp_path / "nonexistent.yaml"
        rs = DesensitizationRuleSet(p)
        # Should not raise; empty rules.
        assert rs._rules == []
        sanitized, hits = rs.apply("张三 13800138000")
        assert sanitized == "张三 13800138000"
        assert hits == []

    def test_apply_substitutes_labels(self, safety_yaml: Path):
        rs = DesensitizationRuleSet(safety_yaml)
        sanitized, hits = rs.apply("张三 13800138000 110101199001011234")
        assert "[REDACTED:PHONE]" in sanitized
        assert "[REDACTED:ID_CARD]" in sanitized
        assert "13800138000" not in sanitized
        assert "110101199001011234" not in sanitized
        assert set(hits) == {"phone_11", "id_card_18"}

    def test_apply_no_match(self, safety_yaml: Path):
        rs = DesensitizationRuleSet(safety_yaml)
        sanitized, hits = rs.apply("TI-RADS 4A 边界清晰")
        assert sanitized == "TI-RADS 4A 边界清晰"
        assert hits == []

    def test_invalid_regex_skipped(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text(
            dedent(
                """\
                rules:
                  - name: "bad"
                    pattern: "[invalid"
                    label: "[REDACTED:XXX]"
                  - name: "good"
                    pattern: "abc"
                    label: "[REDACTED:ABC]"
                """
            ),
            encoding="utf-8",
        )
        rs = DesensitizationRuleSet(p)
        sanitized, hits = rs.apply("abc def")
        assert "[REDACTED:ABC]" in sanitized
        assert "bad" not in hits
        assert "good" in hits


# ----------------------------------------------------------------------
# Desensitizer: heterogeneous redundancy
# ----------------------------------------------------------------------


class TestDesensitizer:
    def test_case_reports_redacts_pii(
        self, safety_yaml: Path, default_yaml: Path
    ):
        d = Desensitizer(
            rules_file_safety=safety_yaml,
            rules_file_default=default_yaml,
        )
        text = "患者张三 13800138000 邮箱 zhangsan@hospital.cn TI-RADS 4A"
        result = d.desensitize(text, source_kind="case_reports")
        assert isinstance(result, DesensitizeResult)
        assert result.skipped is False
        assert "13800138000" not in result.sanitized
        assert PII_LABEL_PHONE in result.sanitized
        assert "zhangsan@hospital.cn" not in result.sanitized
        assert PII_LABEL_EMAIL in result.sanitized
        # safety rules: phone_11 (and id_card_18 if present, but here no ID)
        assert "phone_11" in result.applied_rules
        # default rules: email
        assert "email" in result.applied_rules

    def test_guidelines_skipped(
        self, safety_yaml: Path, default_yaml: Path
    ):
        d = Desensitizer(
            rules_file_safety=safety_yaml,
            rules_file_default=default_yaml,
        )
        text = "TI-RADS classification 13800138000 (phone example)"
        result = d.desensitize(text, source_kind="guidelines")
        assert result.skipped is True
        # Source unchanged
        assert result.sanitized == text
        assert result.applied_rules == []

    def test_other_source_kind_applies(
        self, safety_yaml: Path, default_yaml: Path
    ):
        d = Desensitizer(
            rules_file_safety=safety_yaml,
            rules_file_default=default_yaml,
        )
        result = d.desensitize("13800138000", source_kind="synthetic_data")
        assert result.skipped is False
        assert PII_LABEL_PHONE in result.sanitized

    def test_fallback_patterns_run_when_yaml_missing(self, tmp_path: Path):
        """If both YAML files are missing, fallback patterns still apply."""
        d = Desensitizer(
            rules_file_safety=tmp_path / "missing_a.yaml",
            rules_file_default=tmp_path / "missing_b.yaml",
        )
        result = d.desensitize(
            "phone 13800138000 id 110101199001011234 mail a@b.com",
            source_kind="case_reports",
        )
        assert PII_LABEL_PHONE in result.sanitized
        assert PII_LABEL_ID_CARD in result.sanitized
        assert PII_LABEL_EMAIL in result.sanitized
        # Fallback rule names should be in applied_rules
        assert "phone_11_digits_fb" in result.applied_rules
        assert "id_card_18_chars_fb" in result.applied_rules
        assert "email_fb" in result.applied_rules

    def test_disagreement_detected(
        self, tmp_path: Path, safety_yaml: Path
    ):
        """When only one YAML file has a rule that fires, disagreement is
        recorded in the result."""
        # default_yaml is empty
        empty_default = tmp_path / "empty.yaml"
        empty_default.write_text("rules: []\n", encoding="utf-8")
        d = Desensitizer(
            rules_file_safety=safety_yaml,
            rules_file_default=empty_default,
        )
        result = d.desensitize("13800138000", source_kind="case_reports")
        # phone_11 only in safety
        assert any("only_in_safety" in d for d in result.disagreements)

    def test_no_pii_no_disagreement(
        self, safety_yaml: Path, default_yaml: Path
    ):
        d = Desensitizer(
            rules_file_safety=safety_yaml,
            rules_file_default=default_yaml,
        )
        result = d.desensitize("TI-RADS 4A 边界清晰", source_kind="case_reports")
        assert result.disagreements == []
        assert result.sanitized == "TI-RADS 4A 边界清晰"


# ----------------------------------------------------------------------
# Integration: builder flow uses desensitizer
# ----------------------------------------------------------------------


class TestBuilderIntegration:
    def test_discover_source_docs_invokes_desensitizer(self, tmp_path: Path):
        """P0-RAG-03: KnowledgeIndexBuilderImpl._discover_source_docs
        applies desensitization to case_reports sources before chunking."""
        from app.knowledge import desensitization as desens_mod

        # Replace the default desensitizer with a spy.
        spy_calls: list[tuple[str, str]] = []

        class SpyDesensitizer:
            def desensitize(
                self, content: str, source_kind: str = "case_reports"
            ) -> DesensitizeResult:
                spy_calls.append((content, source_kind))
                if source_kind == "guidelines":
                    return DesensitizeResult(
                        sanitized=content, applied_rules=[], skipped=True
                    )
                return DesensitizeResult(
                    sanitized="SANITIZED:" + content,
                    applied_rules=["spy"],
                )

        from app.knowledge.builder import _discover_source_docs

        source_dir = tmp_path / "thyroid"
        thyroid_dir = source_dir / "thyroid"
        thyroid_dir.mkdir(parents=True)
        (source_dir / "manifest.json").write_text(
            '{"kb_version": "kb_v1.0.0"}', encoding="utf-8"
        )
        (thyroid_dir / "case_001.json").write_text(
            '{"source_doc_id": "case_001", "content": '
            '"患者张三 13800138000 TI-RADS 4A", '
            '"organ": "thyroid", "source_kind": "case_reports"}',
            encoding="utf-8",
        )
        (thyroid_dir / "guideline_001.json").write_text(
            '{"source_doc_id": "guideline_001", "content": '
            '"TI-RADS 4A 边界清晰", '
            '"organ": "thyroid", "source_kind": "guidelines"}',
            encoding="utf-8",
        )

        docs = _discover_source_docs(
            source_dir, organs_filter=["thyroid"],
            desensitizer=SpyDesensitizer(),
        )

        # case_reports: sanitization applied
        case_doc = next(d for d in docs if d.source_doc_id == "case_001")
        assert case_doc.content.startswith("SANITIZED:")
        # Original phone number should still be in the sanitized output
        # (the spy doesn't actually replace PII, it just prefixes "SANITIZED:")
        assert "13800138000" in case_doc.content
        # guidelines: NOT sanitized (skipped)
        guideline_doc = next(
            d for d in docs if d.source_doc_id == "guideline_001"
        )
        assert not guideline_doc.content.startswith("SANITIZED:")
        assert guideline_doc.content == "TI-RADS 4A 边界清晰"
        # Spy was called for both source kinds, but the guidelines
        # result was skipped (sanitization not actually applied)
        spy_kinds = [sk for _, sk in spy_calls]
        assert "case_reports" in spy_kinds
        assert "guidelines" in spy_kinds  # builder always calls; spy decides
