"""A.149：在线 indexer 的 point id 必须用 image_id（不加前缀、不预映射 uuid）。

e2e 实测：传 uuid5(point_id) 会被 qdrant_client 二次 uuid5 → payload.chunk_id 变 uuid
→ 检索侧按 `img_` 前缀分流失败 → 命中被当脏数据丢（image_leg 恒零贡献）。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.services import image_vector_indexer as ivi  # noqa: E402

pytestmark = pytest.mark.track_rag


def test_point_id_helper_removed():
    assert not hasattr(ivi, "_point_id"), "_point_id 已废弃（二次映射根因）"


def test_upsert_uses_image_id():
    src = inspect.getsource(ivi.index_image_async)
    assert "VectorRecord(id=image_id" in src
    assert "VectorRecord(id=_point_id" not in src
