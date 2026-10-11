"""A.51 列表端点契约守卫（设备台账 / 患者检索）。

为什么需要这组"字段集"断言：本轮新增 `PatientListResponse` 时，插入边界
吃掉了相邻的 `DeviceRemoteRecoveryResponse.expires_at`（字段错位到新类上）——
症状是运行期 pydantic ValidationError / remote-recovery 500，静态检查与
import 自检都发现不了。用显式字段集把它钉死。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.models.schemas import (
    DeviceListResponse,
    PatientListItem,
    PatientListResponse,
)


class TestSchemaFieldSets:
    def test_patient_list_response_fields(self):
        assert set(PatientListResponse.model_fields) == {
            "items", "total", "offset", "limit"
        }
        assert set(PatientListItem.model_fields) == {
            "patient_id", "name", "gender", "consent_status", "last_session_at"
        }

    def test_device_list_response_fields(self):
        assert set(DeviceListResponse.model_fields) == {
            "items", "total", "offset", "limit"
        }

    def test_device_remote_recovery_keeps_expires_at(self):
        """远端恢复响应必须带 expires_at（device.py:361 逐字段构造）。"""
        from app.models.schemas import DeviceRemoteRecoveryResponse

        assert "expires_at" in DeviceRemoteRecoveryResponse.model_fields
        obj = DeviceRemoteRecoveryResponse(
            device_id="dev_x", status="active", new_api_key="ak_x", expires_at="2026-01-01T00:00:00"
        )
        assert obj.expires_at == "2026-01-01T00:00:00"

    def test_device_list_item_has_no_api_key(self):
        """台账条目不得有 api_key 字段（管理面零泄漏面）。"""
        from app.models.schemas import DeviceListItem

        assert "api_key" not in DeviceListItem.model_fields


class _StubResult:
    def __init__(self, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _StubSession:
    """按调用序返回：先 count → scalar，再 查询 → rows。"""

    def __init__(self, total: int, rows: list):
        self.total = total
        self.rows = rows
        self.statements: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        if len(self.statements) == 1:
            return _StubResult(scalar=self.total)
        return _StubResult(rows=self.rows)


class TestDeviceListRepository:
    async def test_returns_rows_and_total_with_clamped_limit(self):
        from app.repositories.device_repository import DeviceRepository

        rows = [MagicMock(device_id="dev_1"), MagicMock(device_id="dev_2")]
        session = _StubSession(total=2, rows=rows)
        got_rows, total = await DeviceRepository(session).list_devices(
            status="active", device_type=None, offset=0, limit=999
        )

        assert total == 2
        assert [r.device_id for r in got_rows] == ["dev_1", "dev_2"]
        assert len(session.statements) == 2  # count + 页查询
        # limit 上限 200（防大页拖库）
        compiled = str(session.statements[1].compile(compile_kwargs={"literal_binds": True}))
        assert "LIMIT 200" in compiled.upper()
        # status 过滤同时作用于 count 与页查询
        assert "status" in compiled
        assert "status" in str(
            session.statements[0].compile(compile_kwargs={"literal_binds": True})
        )

    async def test_limit_floor(self):
        from app.repositories.device_repository import DeviceRepository

        session = _StubSession(total=0, rows=[])
        await DeviceRepository(session).list_devices(limit=0)
        compiled = str(session.statements[1].compile(compile_kwargs={"literal_binds": True}))
        assert "LIMIT 1" in compiled.upper()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
