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


def test_dedupe_collapses_frames_of_same_exam():
    """A.82 实测：3 条相似度完全相同且同日 → 是**同一次检查的多帧**，必须合并成 1 条。"""
    from app.services.similar_cases import dedupe_by_exam

    same_exam = [
        SimpleNamespace(score=0.9759, metadata={"image_id": "img_20261009_a", "session_id": "s1"}),
        SimpleNamespace(score=0.9759, metadata={"image_id": "img_20261009_b", "session_id": "s1"}),
        SimpleNamespace(score=0.9700, metadata={"image_id": "img_20261009_c", "session_id": "s1"}),
    ]
    out = dedupe_by_exam(same_exam)
    assert len(out) == 1
    assert out[0].metadata["image_id"] == "img_20261009_a"  # 保留分数最高


def test_dedupe_keeps_distinct_exams_sorted_by_score():
    from app.services.similar_cases import dedupe_by_exam

    hits = [
        SimpleNamespace(score=0.7, metadata={"image_id": "img_20261001_a", "session_id": "s1"}),
        SimpleNamespace(score=0.9, metadata={"image_id": "img_20261005_b", "session_id": "s2"}),
        SimpleNamespace(score=0.8, metadata={"image_id": "img_20261005_c", "session_id": "s3"}),
    ]
    out = dedupe_by_exam(hits)
    # 不同日期 → 不同检查；10-05 的两条（不同 session）按日期合并为一条，取高分
    assert len(out) == 2
    assert out[0].metadata["image_id"] == "img_20261005_b"
    assert out[1].metadata["image_id"] == "img_20261001_a"


def test_dedupe_groups_by_date_even_when_sessions_differ():
    """真机：同一次检查被拆成多个 session（分数全同、同日）——按日期必须合并成 1 条。"""
    from app.services.similar_cases import dedupe_by_exam

    hits = [
        SimpleNamespace(score=0.9759, metadata={"image_id": "img_20261009_a", "session_id": "s1"}),
        SimpleNamespace(score=0.9759, metadata={"image_id": "img_20261009_b", "session_id": "s2"}),
        SimpleNamespace(score=0.9759, metadata={"image_id": "img_20261009_c", "session_id": "s3"}),
    ]
    assert len(dedupe_by_exam(hits)) == 1


def test_dedupe_falls_back_to_date_when_session_missing():
    """payload 没有 session_id 时，同一天的多张按"一次检查"合并（保守，不夸大历史次数）。"""
    from app.services.similar_cases import dedupe_by_exam

    hits = [
        SimpleNamespace(score=0.6, metadata={"image_id": "img_20261009_a"}),
        SimpleNamespace(score=0.5, metadata={"image_id": "img_20261009_b"}),
        SimpleNamespace(score=0.4, metadata={"image_id": "img_20261001_c"}),
    ]
    assert len(dedupe_by_exam(hits)) == 2


def test_min_score_filters_weak_matches():
    """A.82 实测：同病人 top1 有 0.4886 的"不可比"影像 → 低于下限应直接不展示。"""
    from app.services.similar_cases import passes_min_score

    assert passes_min_score(0.4886, 0.55) is False
    assert passes_min_score(0.55, 0.55) is True   # 边界取等号
    assert passes_min_score(0.9759, 0.55) is True
    assert passes_min_score(None, 0.55) is False


def test_default_min_score_is_documented_value():
    from app.config.settings import Settings

    # 暂定值：10 个病人实测 min 0.4886 / 其余 ≥0.63 → 0.55 既能挡住又不误伤
    assert Settings().similar_cases_min_score == 0.55


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
