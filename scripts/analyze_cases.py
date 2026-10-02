#!/usr/bin/env python3
"""
Analyze validation cases for effectiveness and homogeneity.
"""
import sys
from collections import Counter
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.knowledge import BaselineValidator, UDTParser
from app.knowledge.vocabulary import Organ


def analyze_case_homogeneity(cases):
    """Analyze homogeneity of validation cases."""
    if not cases:
        return {}

    # Analyze input text patterns
    text_lengths = [len(c.input_text) for c in cases]
    avg_length = sum(text_lengths) / len(text_lengths)
    min_length = min(text_lengths)
    max_length = max(text_lengths)

    # Analyze expected attributes coverage
    attrs_with_expected = sum(1 for c in cases if c.expected_attributes is not None)
    attrs_with_classification = sum(1 for c in cases if c.expected_classification is not None)

    # Attribute category distribution
    attr_categories = Counter()
    for c in cases:
        if c.expected_attributes:
            for cat in c.expected_attributes:
                attr_categories[cat] += 1

    # Text pattern similarity analysis
    pattern_counts = Counter()
    for c in cases:
        # Check if text follows similar structure
        if "结节" in c.input_text:
            pattern_counts["nodule_pattern"] += 1
        if "大小约" in c.input_text:
            pattern_counts["size_pattern"] += 1
        if "TI-RADS" in c.input_text or "LI-RADS" in c.input_text:
            pattern_counts["classification_pattern"] += 1

    return {
        "total_cases": len(cases),
        "avg_text_length": round(avg_length, 1),
        "min_text_length": min_length,
        "max_text_length": max_length,
        "cases_with_attributes": attrs_with_expected,
        "cases_with_classification": attrs_with_classification,
        "attribute_categories": dict(attr_categories),
        "pattern_distribution": dict(pattern_counts),
        "homogeneity_score": attrs_with_expected / len(cases) if len(cases) > 0 else 0.0,
    }


def analyze_effectiveness(validator, parser, organ):
    """Analyze effectiveness of validation cases."""
    cases = validator.get_cases_by_organ(organ)
    if not cases:
        return {}

    report = validator.validate_udt_parser(parser)

    organ_results = [r for r in report.results if r.case.organ == organ]
    passed = sum(1 for r in organ_results if r.status.name == "PASSED")
    failed = sum(1 for r in organ_results if r.status.name == "FAILED")
    skipped = sum(1 for r in organ_results if r.status.name == "SKIPPED")

    # Analyze error types
    error_types = Counter()
    for r in organ_results:
        for error in r.errors:
            if "Classification mismatch" in error:
                error_types["classification_error"] += 1
            elif "Missing attribute" in error:
                error_types["missing_attribute"] += 1
            elif "Attribute mismatch" in error:
                error_types["attribute_mismatch"] += 1
            else:
                error_types["other_error"] += 1

    return {
        "total_cases": len(cases),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "accuracy": passed / (passed + failed) if (passed + failed) > 0 else 0.0,
        "error_distribution": dict(error_types),
    }


def main():
    print("=" * 80)
    print("VALIDATION CASE ANALYSIS REPORT")
    print("=" * 80)

    validator = BaselineValidator()
    parser = UDTParser()

    organs = [Organ.THYROID, Organ.LIVER, Organ.HEART]

    print("\n" + "=" * 80)
    print("1. HOMOGENEITY ANALYSIS")
    print("=" * 80)

    all_homogeneity = {}
    for organ in organs:
        cases = validator.get_cases_by_organ(organ)
        result = analyze_case_homogeneity(cases)
        all_homogeneity[organ] = result

        print(f"\n{organ}:")
        print(f"  Total cases: {result['total_cases']}")
        print(f"  Avg text length: {result['avg_text_length']} chars")
        print(f"  Text length range: {result['min_text_length']}-{result['max_text_length']}")
        print(f"  Cases with expected attributes: {result['cases_with_attributes']}")
        print(f"  Cases with classification: {result['cases_with_classification']}")
        print(f"  Homogeneity score: {result['homogeneity_score']:.1%}")

        if result["attribute_categories"]:
            print("  Attribute categories:")
            for cat, count in result["attribute_categories"].items():
                print(f"    - {cat}: {count}")

        if result["pattern_distribution"]:
            print("  Pattern distribution:")
            for pattern, count in result["pattern_distribution"].items():
                print(f"    - {pattern}: {count}")

    print("\n" + "=" * 80)
    print("2. EFFECTIVENESS ANALYSIS")
    print("=" * 80)

    all_effectiveness = {}
    for organ in organs:
        result = analyze_effectiveness(validator, parser, organ)
        all_effectiveness[organ] = result

        print(f"\n{organ}:")
        print(f"  Total cases: {result['total_cases']}")
        print(f"  Passed: {result['passed']}")
        print(f"  Failed: {result['failed']}")
        print(f"  Skipped: {result['skipped']}")
        print(f"  Accuracy: {result['accuracy']:.1%}")

        if result.get("error_distribution"):
            print("  Error distribution:")
            for error_type, count in result["error_distribution"].items():
                print(f"    - {error_type}: {count}")

    print("\n" + "=" * 80)
    print("3. SUMMARY ASSESSMENT")
    print("=" * 80)

    print("\nEffectiveness Scorecard:")
    print("-" * 40)
    print(f"{'Organ':<20} {'Cases':>6} {'Attr Cov':>10} {'Accuracy':>10}")
    print("-" * 40)
    for organ in organs:
        homo = all_homogeneity[organ]
        eff = all_effectiveness[organ]
        attr_cov = f"{homo['cases_with_attributes']}/{homo['total_cases']}"
        print(f"{organ.name:<20} {homo['total_cases']:>6} {attr_cov:>10} {eff['accuracy']:>10.1%}")

    print("\nKey Findings:")
    print("-" * 40)

    # Assess thyroid
    if all_homogeneity[Organ.THYROID]['homogeneity_score'] > 0.9:
        print("✓ Thyroid cases: HIGH homogeneity")
    else:
        print("⚠ Thyroid cases: moderate homogeneity")

    # Assess liver
    if all_homogeneity[Organ.LIVER]['homogeneity_score'] > 0.9:
        print("✓ Liver cases: HIGH homogeneity")
    else:
        print("⚠ Liver cases: moderate homogeneity")

    # Assess heart
    heart_homo = all_homogeneity[Organ.HEART]['homogeneity_score']
    if heart_homo == 0:
        print("✗ Heart cases: NO expected attributes defined - ALL SKIPPED")
    elif heart_homo < 0.5:
        print("⚠ Heart cases: low attribute coverage")

    # Overall assessment
    total_valid = sum(h['cases_with_attributes'] for h in all_homogeneity.values())
    total_cases = sum(h['total_cases'] for h in all_homogeneity.values())
    overall_attr_cov = total_valid / total_cases

    print(f"\nOverall attribute coverage: {total_valid}/{total_cases} ({overall_attr_cov:.1%})")

    if overall_attr_cov >= 0.7:
        print("Assessment: PASS - meets baseline validation requirements")
    elif overall_attr_cov >= 0.5:
        print("Assessment: WARNING - partial compliance")
    else:
        print("Assessment: FAIL - needs improvement")


if __name__ == "__main__":
    sys.exit(main())
