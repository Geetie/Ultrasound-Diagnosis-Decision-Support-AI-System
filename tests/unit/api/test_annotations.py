"""标注存档端点（A.40）：GET/PUT 往返 + 防路径穿越 + 非法 id 拒绝。

不走真 app（依赖 DB/审计链），直接打路径函数与模型校验 —— 端点本身薄，
关键是 `_annotation_path` 的确定性推导与 id 校验（与 dicom_storage 同口径）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.v1.annotations import AnnotationRecord, _annotation_path, get_annotations
from app.api.v1.errors import NotFoundException


def test_annotation_path_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.api.v1.annotations.settings.dicom_storage_path", str(tmp_path))
    p = _annotation_path("img_20261009_ab12cd34")
    assert p is not None
    assert p.parent == tmp_path / "annotations"
    assert p.name == "img_20261009_ab12cd34.json"
    # 同 id 同路径（确定性推导，无索引）
    assert _annotation_path("img_20261009_ab12cd34") == p


def test_annotation_path_rejects_bad_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.api.v1.annotations.settings.dicom_storage_path", str(tmp_path))
    # 路径穿越 / 任意串一律 None（防把 JSON 写到影像目录外）
    assert _annotation_path("../../etc/passwd") is None
    assert _annotation_path("not-an-id") is None


@pytest.mark.asyncio
async def test_get_annotations_missing_file_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("app.api.v1.annotations.settings.dicom_storage_path", str(tmp_path))
    result = await get_annotations("img_20261009_ab12cd34", current_user={"username": "d1"})
    assert result == {"image_id": "img_20261009_ab12cd34", "annotations": []}


@pytest.mark.asyncio
async def test_get_annotations_invalid_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.api.v1.annotations.settings.dicom_storage_path", str(tmp_path))
    with pytest.raises(NotFoundException):
        await get_annotations("../evil", current_user=None)


def test_record_rejects_bad_image_id():
    with pytest.raises(ValidationError):
        AnnotationRecord(image_id="../evil", annotations=[])
