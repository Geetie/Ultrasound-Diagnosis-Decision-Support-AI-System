#!/usr/bin/env python3
"""
Test script for Knowledge Base module.
Demonstrates the usage of UDT parser, vocabulary, and baseline validation.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.knowledge import BaselineValidator, UDAFVocabulary, UDTParser, parse_kb_version
from app.knowledge.vocabulary import Organ


def test_vocabulary():
    print("=" * 60)
    print("Testing UDAF Vocabulary")
    print("=" * 60)

    vocab = UDAFVocabulary()

    test_terms = ["高回声", "低回声", "边界清", "微钙化"]
    print("\n1. Term Normalization:")
    for term in test_terms:
        normalized = vocab.normalize(term)
        print(f"   '{term}' -> '{normalized}'")

    print("\n2. Synonyms:")
    synonyms = vocab.get_synonyms("高回声")
    print(f"   'hyperechoic' synonyms: {synonyms}")

    print("\n3. Coverage Rate:")
    coverage = vocab.coverage_rate(test_terms + ["unknown_term"])
    print(f"   Coverage: {coverage:.1%}")

    print("\nOK - Vocabulary test completed!")


def test_udt_parser():
    print("\n" + "=" * 60)
    print("Testing UDT Parser")
    print("=" * 60)

    parser = UDTParser()

    test_cases = [
        "甲状腺右叶中极低回声结节，边界清，形态规则，大小约1.2x0.8 cm，未见明显血流信号，TI-RADS 3",
        "甲状腺左叶下极低回声结节，边界不清，形态不规则，内见微钙化，血流丰富。TI-RADS 4C",
        "肝右叶见高回声结节，边界清，大小约2.0x1.5 cm。"
    ]

    for i, text in enumerate(test_cases, 1):
        print(f"\nTest Case {i}:")
        print(f"Input: {text}")

        result = parser.parse(text)

        print(f"Organ detected: {result.organ}")
        print(f"Attributes found: {len(result.attributes)}")

        for attr in result.attributes:
            print(f"  - {attr.category}: {attr.value} (from: '{attr.raw_text}')")

        print(f"Coverage rate: {result.coverage_rate:.1%}")

    print("\nOK - UDT Parser test completed!")


def test_version_management():
    print("\n" + "=" * 60)
    print("Testing Version Management")
    print("=" * 60)

    version_strings = [
        "kb_v1.2.3",
        "kb_v2.0.0~hospital_A",
        "invalid_version"
    ]

    for vs in version_strings:
        version = parse_kb_version(vs)
        if version:
            print(f"\n'{vs}' -> major={version.major}, minor={version.minor}, patch={version.patch}, hospital={version.hospital_id}")
            print(f"  String representation: {version}")
        else:
            print(f"\n'{vs}' -> Failed to parse")

    print("\nOK - Version management test completed!")


def test_baseline_validation():
    print("\n" + "=" * 60)
    print("Testing Baseline Validation")
    print("=" * 60)

    validator = BaselineValidator()
    parser = UDTParser()

    print(f"\nLoaded {len(validator.cases)} validation cases")
    print(f"Thyroid cases: {len(validator.get_cases_by_organ(Organ.THYROID))}")
    print(f"Liver cases: {len(validator.get_cases_by_organ(Organ.LIVER))}")
    print(f"Heart cases: {len(validator.get_cases_by_organ(Organ.HEART))}")

    print("\nRunning validation...")
    report = validator.validate_udt_parser(parser)

    print("\nValidation Report:")
    print(f"  Total cases: {report.total_cases}")
    print(f"  Passed: {report.passed_cases}")
    print(f"  Failed: {report.failed_cases}")
    print(f"  Accuracy: {report.accuracy:.1%}")
    print(f"  Duration: {report.duration_seconds:.2f}s")

    print("\nOrgan Breakdown:")
    for organ, stats in report.organ_breakdown.items():
        organ_acc = stats['passed'] / stats['total'] if stats['total'] > 0 else 0
        print(f"  {organ}: {stats['passed']}/{stats['total']} ({organ_acc:.1%})")

    print("\nOK - Baseline validation test completed!")


def main():
    print("\n" + "=" * 60)
    print("KNOWLEDGE BASE MODULE - TEST SUITE")
    print("=" * 60)

    try:
        test_vocabulary()
        test_udt_parser()
        test_version_management()
        test_baseline_validation()

        print("\n" + "=" * 60)
        print("ALL TESTS COMPLETED SUCCESSFULLY!")
        print("=" * 60)

    except Exception as e:
        print(f"\nError during testing: {e!s}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
