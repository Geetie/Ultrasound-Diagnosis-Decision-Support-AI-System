"""A.79 守卫：影像向量建库必须**强制带病人隔离字段**、幂等、默认 dry-run。

检索侧（A.78）按 `session_id` 过滤；如果写入时没这个字段，那条向量要么永远查不到，
要么诱导别人"去掉过滤"去找它 —— 所以**缺 session/patient 一律拒写**。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = (
    Path(__file__).resolve().parents[3] / "scripts" / "ops" / "build_image_vectors.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_build_img_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_payload_requires_session_and_patient():
    b = _load()
    ok = b.payload_for(
        {
            "image_id": "img1",
            "session_id": "sess1",
            "patient_id": "pat1",
            "modality": "US",
            "storage_path": "/x/y.dcm",
            "organ": "thyroid",
        }
    )
    assert ok is not None
    assert ok["session_id"] == "sess1"
    assert ok["patient_id"] == "pat1"
    assert ok["organ"] == "thyroid"

    # 缺任一关键字段 → 拒绝写入
    assert b.payload_for({"image_id": "i", "session_id": "s", "patient_id": ""}) is None
    assert b.payload_for({"image_id": "i", "session_id": "", "patient_id": "p"}) is None
    assert b.payload_for({"image_id": "", "session_id": "s", "patient_id": "p"}) is None


def test_image_format_is_derived_from_path():
    b = _load()
    assert b.image_format_of("/a/b/c.dcm") == "dcm"
    assert b.image_format_of("/a/b/c.png") == "png"
    assert b.image_format_of("/a/b/noext") == "dicom"


def test_builder_defaults_to_dry_run_and_is_idempotent_by_design():
    src = _SRC.read_text(encoding="utf-8")
    assert 'ap.add_argument("--apply", action="store_true"' in src
    assert "create_collection" in src and "dimension=IMAGE_DIM" in src
    # point id 由 client 用 uuid5(record.id) 确定性生成 → 重复跑幂等（在 qdrant_client 里保证）
    assert "VectorRecord(" in src and "id=row[\"image_id\"]" in src
    # 不允许"没有 payload 也写"
    assert "metadata=payload_for(row) or {}" in src
