"""A.144 / G2：影像向量 payload 携带 8003 结构化视觉输出。

用户指出服务器上已有视觉模型（UltraSoundCLIP，8003：T2a /v1/classify +
T2b /v1/analyze_view）。CLIP 不产自由文本 caption，但它的**结构化输出**
（切面标签、三分类概率、质量分）是真实的视觉事实——正适合填
`visual_features` 通道（A.137 铺的），且不会编造所见。

本组守卫：
1) payload_for 带 features → 写入 visual_features + 来源标注；不带 → 与 A.79 一致；
2) None 值字段不进 payload（不造假字段）；
3) 缺 session/patient 仍拒写（病人隔离闸不被绕过）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from scripts.ops.build_image_vectors import payload_for  # noqa: E402

pytestmark = pytest.mark.track_rag


def _row(**over):
    base = {
        "image_id": "img_1",
        "session_id": "s1",
        "patient_id": "p1",
        "modality": "B-mode",
        "storage_path": "/data/x.png",
        "organ": "thyroid",
    }
    base.update(over)
    return base


def test_payload_with_features_carries_visual_fields():
    features = {"t2a_label": "benign", "t2a_prob_benign": 0.87, "t2b_view": "transverse", "t2b_quality": 4.1}
    payload = payload_for(_row(), features)
    assert payload["visual_features"] == [
        "t2a_label=benign",
        "t2a_prob_benign=0.87",
        "t2b_quality=4.1",
        "t2b_view=transverse",
    ]
    assert payload["visual_features_source"] == "ultrasound_clip_endpoints"


def test_payload_without_features_matches_a79():
    payload = payload_for(_row())
    assert "visual_features" not in payload
    assert "visual_features_source" not in payload


def test_none_values_dropped():
    payload = payload_for(_row(), {"t2a_label": None, "t2b_view": "axial"})
    assert payload["visual_features"] == ["t2b_view=axial"]


def test_empty_features_means_no_key():
    assert "visual_features" not in payload_for(_row(), {})


def test_isolation_gate_still_rejects():
    assert payload_for(_row(session_id=""), {"t2a_label": "x"}) is None
    assert payload_for(_row(patient_id=""), {"t2a_label": "x"}) is None
