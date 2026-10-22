"""A.86 守卫：分割"未做"的原因必须**说真话且可行动**。

真机教训：走查里医生看到"未做（image_bytes_unavailable）"，实际那三张影像都是
**二次采集（屏幕截图）**，本来就不该勾画。归因错误会让医生白跑去找技师重传。
"""

from __future__ import annotations

from pathlib import Path

from app.services.diagnosis_pipeline import (
    SEGMENTATION_REASON_TEXT,
    segmentation_reason_text,
)

_SRC = (
    Path(__file__).resolve().parents[3] / "app" / "services" / "diagnosis_pipeline.py"
)


def test_secondary_capture_explains_why_and_what_to_do():
    text = segmentation_reason_text("secondary_capture_burned_in")
    assert "屏幕截图" in text
    assert "原始 DICOM" in text          # 告诉医生下一步怎么做
    assert "未做病灶勾画" in text


def test_missing_bytes_points_to_the_technician():
    text = segmentation_reason_text("image_bytes_unavailable")
    assert "技师" in text and "重新上传" in text


def test_unknown_reason_falls_back_without_inventing():
    text = segmentation_reason_text("some_new_code")
    assert "未做病灶勾画" in text
    assert "some_new_code" in text       # 把原始码保留，便于排查


def test_empty_reason_is_still_readable():
    assert segmentation_reason_text(None) == "本次未做病灶勾画"
    assert segmentation_reason_text("  ") == "本次未做病灶勾画"


def test_all_mapped_reasons_are_chinese_and_actionable():
    for code, text in SEGMENTATION_REASON_TEXT.items():
        assert text.startswith("本次未做病灶勾画"), code
        assert any(ch in text for ch in "请该如需要"), f"{code} 缺少可行动信息"


def test_pipeline_records_per_candidate_skip_reason():
    """源码级：每个 continue 必须带 skips 记录，且 fallback 不再写死 image_bytes_unavailable。"""
    src = _SRC.read_text(encoding="utf-8")
    assert 'skips.append({"image_id": cand_row.image_id, "reason": "image_bytes_unavailable"})' in src
    assert 'skips.append({"image_id": cand_row.image_id, "reason": deny_reason})' in src
    # 旧写法：best is None 时一律报取不到字节
    assert '"reason": "image_bytes_unavailable",\n                "image_id": rows[0].image_id,' not in src
    assert "reason_text" in src
