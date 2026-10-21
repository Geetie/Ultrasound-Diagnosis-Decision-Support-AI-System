"""A.82（对外面）守卫：相似病例在**接口与报告**里必须带"参考"标注、且无病患标识。

为什么单独测这层：内部实现再干净，只要某个出口漏了标注，医生就会把它读成诊断依据。
三个出口都要管住：①结果接口（`response_block`）②报告正文（`render_report`）③取消/失败分支。
"""

from __future__ import annotations

from app.services.similar_cases import CASE_DISCLAIMER, CASE_LABEL, response_block


def test_response_block_labels_and_keeps_cases():
    block = response_block(
        {
            "status": "ok",
            "scope": "self",
            "cases": [{"image_id": "img_1", "date": "2026-10-09", "score": 0.91}],
        }
    )
    assert block["label"] == CASE_LABEL
    assert block["disclaimer"] == CASE_DISCLAIMER
    assert block["count"] == 1
    assert block["cases"][0]["image_id"] == "img_1"
    assert "参考" in block["label"]


def test_response_block_hides_cases_when_not_ok():
    block = response_block({"status": "unavailable", "reason": "InferenceError", "cases": [{"image_id": "x"}]})
    assert block["status"] == "unavailable"
    assert block["count"] == 0
    assert block["cases"] == []
    assert block["reason"] == "InferenceError"


def test_response_block_handles_none_and_unknown_status():
    assert response_block(None)["status"] == "not_run"
    assert response_block({})["count"] == 0
    # 任何情况下都带标注
    assert response_block(None)["label"] == CASE_LABEL


def test_report_section_carries_disclaimer_and_no_identifiers():
    from app.services.report_service import (
        SIMILAR_CASES_HEADING,
        _format_similar_cases,
        render_report,
    )

    text = _format_similar_cases(
        {
            "status": "ok",
            "cases": [
                {"image_id": "img_20261009_1", "date": "2026-10-09", "score": 0.9759},
                {"image_id": "img_20261009_2", "score": 0.9},
            ],
        }
    )
    assert "- 2026-10-09（编号 img_20261009_1，相似度 0.976）" in text
    assert "- 日期未知（编号 img_20261009_2，相似度 0.900）" in text
    assert "PATIENT_" not in text and "sess_" not in text

    rendered = render_report(
        {
            "organ": "thyroid",
            "similar_cases_text": text,
            "evidence": "（无）",
            "organ_label": "甲状腺",
        }
    )
    assert SIMILAR_CASES_HEADING in rendered
    assert "不构成诊断依据" in rendered


def test_report_omits_section_when_no_cases():
    from app.services.report_service import (
        SIMILAR_CASES_HEADING,
        _format_similar_cases,
        render_report,
    )

    assert _format_similar_cases({"status": "unavailable"}) == ""
    assert _format_similar_cases(None) == ""
    rendered = render_report({"organ": "thyroid", "similar_cases_text": "", "evidence": "（无）"})
    assert SIMILAR_CASES_HEADING not in rendered
