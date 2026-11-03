"""A.147 / G2 收口：新上传影像在线进向量库。

缺口：build_image_vectors.py 是离线脚本，只覆盖历史库；新上传影像按 session
过滤恒 0 命中（e2e 实测 image_leg_no_hits）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.services.image_vector_indexer import _point_id, index_image_async  # noqa: E402

pytestmark = pytest.mark.track_rag


def test_point_id_is_deterministic():
    assert _point_id("img_1") == _point_id("img_1")
    assert _point_id("img_1") != _point_id("img_2")


@pytest.mark.asyncio
async def test_rejects_without_isolation_fields():
    assert await index_image_async(
        image_id="i", session_id="", patient_id="p", storage_path="/x"
    ) is False
    assert await index_image_async(
        image_id="i", session_id="s", patient_id="", storage_path="/x"
    ) is False


@pytest.mark.asyncio
async def test_file_missing_fails_gracefully():
    ok = await index_image_async(
        image_id="i", session_id="s", patient_id="p", storage_path="/nonexistent/x.dcm"
    )
    assert ok is False
