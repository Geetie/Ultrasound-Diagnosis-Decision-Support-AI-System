"""specialist factory：load_organ_thresholds DB 路径与 build_dispatcher 降级分支。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.track_agt]

import app.agents.specialist.factory as mod
from app.agents.specialist.factory import (
    build_dispatcher,
    build_dispatcher_with_thresholds,
    load_organ_thresholds,
)


class _AsyncCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


def _db_with_rows(rows):
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: rows)
        )
    )
    return db


class TestLoadOrganThresholds:
    async def test_mixed_rows_organ_and_disease_level(self, monkeypatch):
        rows = [
            SimpleNamespace(organ="thyroid", disease_type=None, confidence_threshold=0.85),
            SimpleNamespace(organ="liver", disease_type="肝细胞癌", confidence_threshold=0.92),
            SimpleNamespace(organ="liver", disease_type="血管瘤", confidence_threshold=0.7),
        ]
        monkeypatch.setattr(
            "app.config.database.AsyncSessionLocal",
            lambda: _AsyncCtx(_db_with_rows(rows)),
        )
        result = await load_organ_thresholds()
        assert result["thyroid"][0] == 0.85
        assert result["liver"][0] == 0.0  # liver 无器官级行 → 默认 0
        assert result["liver"][1] == {"肝细胞癌": 0.92, "血管瘤": 0.7}
        # 未配置器官补默认
        assert "cardiac" in result

    async def test_empty_table_fills_defaults(self, monkeypatch):
        monkeypatch.setattr(
            "app.config.database.AsyncSessionLocal",
            lambda: _AsyncCtx(_db_with_rows([])),
        )
        result = await load_organ_thresholds()
        assert set(result) == set(mod.DEFAULT_ORGAN_THRESHOLDS)
        assert all(d == {} for _, d in result.values())

    async def test_db_failure_falls_back_to_defaults(self, monkeypatch):
        class _BoomCtx:
            async def __aenter__(self):
                raise RuntimeError("db down")

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(
            "app.config.database.AsyncSessionLocal", lambda: _BoomCtx()
        )
        result = await load_organ_thresholds()
        assert set(result) == set(mod.DEFAULT_ORGAN_THRESHOLDS)


class TestBuildDispatcherDegraded:
    async def test_placeholder_on_missing_dependencies(self):
        d = build_dispatcher(None, None, use_placeholder_on_missing=True)
        assert d is not None

    async def test_strict_mode_raises_on_missing(self):
        with pytest.raises(ValueError, match="use_placeholder_on_missing"):
            build_dispatcher(None, None, use_placeholder_on_missing=False)

    async def test_build_with_thresholds(self, monkeypatch):
        engine = MagicMock()
        kagent = MagicMock()
        monkeypatch.setattr(
            mod,
            "load_organ_thresholds",
            AsyncMock(return_value={"thyroid": (0.9, {"甲亢": 0.8})}),
        )
        d = await build_dispatcher_with_thresholds(engine, kagent)
        assert d is not None
