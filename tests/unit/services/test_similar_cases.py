"""A.82 守卫：相似病例通道（影像向量的正确用法）。

三条不能破的规矩：
1. **合规默认**：`scope="self"` 只查同一病人；缺 patient_id 时**不查**（绝不退化成全库）；
2. **脱敏输出**：返回字段里**不许**出现 `patient_id` / `session_id`；
3. **独立通道**：不写进 `knowledge_ids`、不进 LLM 知识上下文（否则又回到"伪装成知识块"）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services.similar_cases import cases_filter, public_case, summary_of

_REPO = Path(__file__).resolve().parents[3]


def test_self_scope_requires_patient_id():
    assert cases_filter("self", "pat-1", "thyroid") == {"patient_id": "pat-1"}
    # 缺 patient_id → 不查（返回 None，调用方据此跳过）
    assert cases_filter("self", None, "thyroid") is None
    assert cases_filter("self", "   ", "thyroid") is None


def test_deidentified_scope_filters_by_organ_only():
    assert cases_filter("deidentified", "pat-1", "cardiac") == {"organ": "cardiac"}
    assert cases_filter("deidentified", None, None) is None
    assert cases_filter("unknown-scope", "pat-1", "liver") is None


def test_public_case_never_leaks_patient_or_session():
    hit = SimpleNamespace(
        score=0.91,
        metadata={
            "image_id": "img_1",
            "patient_id": "PATIENT_001",
            "session_id": "sess-1",
            "organ": "thyroid",
            "date": "2026-10-01",
        },
    )
    out = public_case(hit)
    assert out["image_id"] == "img_1"
    assert out["organ"] == "thyroid"
    assert out["score"] == 0.91
    assert "patient_id" not in out and "session_id" not in out


def test_date_is_derived_from_image_id_when_payload_lacks_it():
    """建库 payload 没写 date → 从 `img_YYYYMMDD_…` 推导，不必重建 580 条向量。"""
    from app.services.similar_cases import date_of

    assert date_of("img_20261009_6e366343") == "2026-10-09"
    assert date_of("img_20261009_x", {"date": "2026-10-10"}) == "2026-10-10"
    assert date_of("weird-id") is None
    assert date_of("") is None

    hit = SimpleNamespace(score=0.8, metadata={"image_id": "img_20261009_6e366343"})
    assert public_case(hit)["date"] == "2026-10-09"


def test_public_case_tolerates_missing_metadata():
    out = public_case(SimpleNamespace(score=0.5, metadata=None))
    assert out == {"score": 0.5}


def test_summary_reports_count_only_when_ok():
    assert summary_of({"status": "ok", "scope": "self", "cases": [{"score": 0.9}]}) == {
        "status": "ok",
        "count": 1,
        "top_score": 0.9,
        "scope": "self",
    }
    assert summary_of({"status": "unavailable", "cases": [{"score": 0.9}]}) == {
        "status": "unavailable",
        "count": 0,
    }


def test_pipeline_wires_similar_cases_as_separate_field():
    src = (_REPO / "app" / "services" / "diagnosis_pipeline.py").read_text(encoding="utf-8")
    assert "_safe_similar_cases(request)" in src
    assert '"similar_cases": similar_cases' in src
    # 不许把相似病例混进知识引用
    assert "knowledge_ids.append" not in src or "similar_cases" not in src.split(
        "knowledge_ids.append"
    )[1][:200]


def test_settings_default_to_self_scope():
    from app.config.settings import Settings

    s = Settings()
    assert s.similar_cases_scope == "self"
    assert s.similar_cases_enabled is True
