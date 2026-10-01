"""UDT→ICD-10 映射模块单测。"""
from pathlib import Path

from app.knowledge.udt_icd import (
    available_codes,
    codes_by_organ,
    udt_to_icd10,
    udt_to_icd10_reported,
)


def test_known_mapping_thyroid_hyperthyroidism():
    m = udt_to_icd10("thyroid_hyperthyroidism")
    assert m is not None
    assert m.icd10.startswith("E05")
    assert m.confidence in ("exact", "multi", "manual", "manual_pending_review")


def test_known_mapping_liver_cirrhosis():
    m = udt_to_icd10("liver_cirrhosis")
    assert m is not None
    assert m.icd10.startswith("K74")


def test_all_30_codes_have_icd10():
    codes = available_codes()
    assert len(codes) == 30
    for c in codes:
        m = udt_to_icd10(c)
        assert m is not None and m.icd10


def test_unknown_code_returns_none():
    assert udt_to_icd10("nonexistent_disease_xyz") is None


def test_map_file_exists_in_repo():
    p = Path("data/medical_resources/udt_icd_map.json")
    assert p.exists()


def test_reported_excludes_pending():
    # 右心衰（底表无独立码）映射为 manual_pending_review，
    # 不得进入正式统计上报口径
    assert udt_to_icd10_reported("cardiac_right_heart_failure_chronic") is None
    # 常规映射可上报
    assert udt_to_icd10_reported("thyroid_hyperthyroidism") is not None


def test_codes_by_organ():
    thyroid = codes_by_organ("thyroid")
    assert thyroid and all(c.startswith("thyroid_") for c in thyroid)
