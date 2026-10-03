"""DicomImageRepository 单测（内存 SQLite 不用 PG——只测查询形状，故用 fake session）。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.dicom_image_repository import DicomImageRepository

pytestmark = [pytest.mark.asyncio]


def _rows(*ids_paths):
    return [
        SimpleNamespace(
            image_id=i,
            session_id="s1",
            patient_id="P1",
            storage_path=p,
            sop_instance_uid=f"1.2.3.{n}",
            modality="US",
            width=512,
            height=400,
            frame_count=18,
            size_bytes=1024,
            original_filename=f"{i}.dcm",
            created_at=None,
        )
        for n, (i, p) in enumerate(ids_paths)
    ]


def _repo(result):
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.scalars = AsyncMock(return_value=result)
    return DicomImageRepository(db), db


class TestQueries:
    async def test_list_by_session_returns_rows(self):
        rows = _rows(("img_1", "/a/1.dcm"), ("img_2", "/a/2.dcm"))
        repo, db = _repo(rows)
        assert await repo.list_by_session("s1") == rows
        assert db.scalars.await_count == 1

    async def test_filter_existing_preserves_input_order(self):
        """按入参顺序返回（不是 DB 顺序）——调用方按 image_ids 语义取图。"""
        rows = _rows(("img_2", "/a/2.dcm"), ("img_1", "/a/1.dcm"))
        repo, _ = _repo(rows)
        got = await repo.filter_existing_in_session("s1", ["img_1", "img_2"])
        assert [r.image_id for r in got] == ["img_1", "img_2"]

    async def test_filter_existing_drops_unknown(self):
        rows = _rows(("img_1", "/a/1.dcm"))
        repo, _ = _repo(rows)
        got = await repo.filter_existing_in_session("s1", ["img_1", "img_9"])
        assert [r.image_id for r in got] == ["img_1"]

    async def test_filter_existing_empty_input_short_circuits(self):
        repo, db = _repo([])
        assert await repo.filter_existing_in_session("s1", []) == []
        assert db.scalars.await_count == 0

    async def test_missing_from_session(self):
        rows = _rows(("img_1", "/a/1.dcm"))
        repo, _ = _repo(rows)
        missing = await repo.missing_from_session("s1", ["img_1", "img_x", "img_y"])
        assert missing == ["img_x", "img_y"]

    async def test_storage_paths_for(self):
        rows = _rows(("img_1", "/a/1.dcm"), ("img_2", "/a/2.dcm"))
        repo, _ = _repo(rows)
        assert await repo.storage_paths_for(["img_2", "img_1"]) == {
            "img_2": "/a/2.dcm",
            "img_1": "/a/1.dcm",
        }

    async def test_storage_paths_for_empty(self):
        repo, db = _repo([])
        assert await repo.storage_paths_for([]) == {}
        assert db.scalars.await_count == 0


class TestToDict:
    def test_serializes_created_at(self):
        import datetime as dt

        row = _rows(("img_1", "/a/1.dcm"))[0]
        row.created_at = dt.datetime(2026, 10, 3, 12, 0, tzinfo=dt.timezone.utc)
        d = DicomImageRepository.to_dict(row)
        assert d["image_id"] == "img_1"
        assert d["storage_path"] == "/a/1.dcm"
        assert d["created_at"].startswith("2026-10-03T12:00")
