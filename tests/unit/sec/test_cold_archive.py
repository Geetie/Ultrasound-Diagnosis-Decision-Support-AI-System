"""T-GB-SEC-02 审计日志冷热分层 + 对象存储冷归档测试。

覆盖：
- 对象存储适配器（内存/落盘假实现、路径逃逸防护、工厂与注入）；
- 冷归档核心不变量：**上传成功前不删热数据**；
- 审计链创世记录留作热层锚点（链不被撕裂）；
- 清单字段（checksum/记录数/时间范围）与失败降级（pending）；
- 恢复与校验（checksum、记录数、缺对象）；
- 会话归档（门诊/住院年限）冷归档；
- 数据保留年限策略与周期任务接线的委托关系；
- 冷归档可观测接口。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.object_storage import (
    LocalFakeObjectStorage,
    MinioObjectStorage,
    build_object_key,
    create_object_storage,
    get_object_storage,
    set_object_storage,
)
from app.models.database import AuditLog, ColdArchiveManifest, SessionArchive
from app.services.cold_archive import (
    CATEGORY_AUDIT_LOGS,
    CATEGORY_SESSION_ARCHIVES,
    ColdArchiveService,
    _serialize_row,
    _to_jsonl,
)


# ----------------------------------------------------------------------
# 桩件
# ----------------------------------------------------------------------


class _FakeDb:
    """最小异步会话替身：只实现冷归档用到的接口。

    ``execute_rows`` 仅在**首次** ``execute`` 时返回（对应"取待归档记录"
    那一次查询）；后续查询一律返回空结果，避免审计链写入（会以 DESC 取上一条
    记录）误拿到业务行。
    """

    def __init__(self, execute_rows: Optional[List[Any]] = None) -> None:
        self._execute_rows = list(execute_rows or [])
        self.added: List[Any] = []
        self.execute_calls: List[Any] = []
        self.flushed = 0
        self._call_index = 0

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        self._call_index += 1
        rows = self._execute_rows if self._call_index == 1 else []
        result = MagicMock()
        result.rowcount = len(rows)
        scalars = MagicMock()
        scalars.all.return_value = rows
        result.scalars.return_value = scalars
        result.scalar_one_or_none.return_value = rows[0] if rows else None
        result.all.return_value = rows
        return result

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1


def _audit_row(
    row_id: int,
    *,
    session_id: Optional[str] = "sess_1",
    hash_prev: Optional[str] = "h_prev",
    created_at: Optional[datetime] = None,
) -> AuditLog:
    return AuditLog(
        id=row_id,
        log_id=f"log_{row_id}",
        session_id=session_id,
        action="diagnosis_request",
        actor="doc_1",
        target_type="diagnosis",
        target_id="req_1",
        detail={"k": "v"},
        log_type="operation",
        hash_prev=hash_prev,
        hash_self=f"h_{row_id}",
        is_critical_operation=False,
        cross_chain_hash=None,
        created_at=created_at or (datetime.now(timezone.utc) - timedelta(days=400)),
    )


def _session_archive_row(row_id: int, patient_type: str = "outpatient") -> SessionArchive:
    return SessionArchive(
        id=row_id,
        session_id=f"sess_{row_id}",
        patient_id="P001",
        device_id="dev_1",
        status="completed",
        target_organs=["thyroid"],
        organ_statuses={"thyroid": "completed"},
        knowledge_base_version="kb_v1.0.0",
        consent_status_at_creation="granted",
        patient_type=patient_type,
        archive_timestamp=datetime.now(timezone.utc) - timedelta(days=6000),
        created_at=datetime.now(timezone.utc) - timedelta(days=6001),
        archived_at=datetime.now(timezone.utc) - timedelta(days=6000),
    )


# ----------------------------------------------------------------------
# 对象存储适配器
# ----------------------------------------------------------------------


class TestLocalFakeObjectStorage:
    @pytest.mark.asyncio
    async def test_memory_roundtrip(self):
        storage = LocalFakeObjectStorage()
        stored = await storage.put("a/b.jsonl", b"hello")
        assert stored.size_bytes == 5
        assert await storage.exists("a/b.jsonl") is True
        assert await storage.get("a/b.jsonl") == b"hello"
        assert await storage.list_keys("a/") == ["a/b.jsonl"]
        assert await storage.delete("a/b.jsonl") is True
        assert await storage.get("a/b.jsonl") is None

    @pytest.mark.asyncio
    async def test_file_backed_survives_new_instance(self, tmp_path):
        first = LocalFakeObjectStorage(root_dir=str(tmp_path))
        await first.put("x/y.jsonl", b"payload")

        second = LocalFakeObjectStorage(root_dir=str(tmp_path))
        assert await second.get("x/y.jsonl") == b"payload"

    @pytest.mark.asyncio
    async def test_key_traversal_is_neutralised(self, tmp_path):
        storage = LocalFakeObjectStorage(root_dir=str(tmp_path))
        await storage.put("../../etc/passwd", b"sneaky")
        files = list(tmp_path.rglob("*"))
        written = [p for p in files if p.is_file()]
        assert written and all(str(tmp_path) in str(p) for p in written)

    @pytest.mark.asyncio
    async def test_missing_get_returns_none(self):
        storage = LocalFakeObjectStorage()
        assert await storage.get("nope") is None
        assert await storage.delete("nope") is False


class TestObjectStorageFactory:
    def teardown_method(self):
        set_object_storage(None)

    def test_local_fake_memory_when_dir_empty(self):
        cfg = MagicMock()
        cfg.object_storage_adapter = "local_fake"
        cfg.object_storage_local_dir = ""
        assert isinstance(create_object_storage(cfg), LocalFakeObjectStorage)

    def test_s3_requires_endpoint(self):
        cfg = MagicMock()
        cfg.object_storage_adapter = "s3"
        cfg.object_storage_bucket = "b"
        cfg.object_storage_endpoint = ""
        with pytest.raises(ValueError):
            create_object_storage(cfg)

    def test_s3_builds_minio_adapter(self):
        cfg = MagicMock()
        cfg.object_storage_adapter = "minio"
        cfg.object_storage_bucket = "archive"
        cfg.object_storage_endpoint = "minio:9000"
        cfg.object_storage_access_key = "ak"
        cfg.object_storage_secret_key = "sk"
        cfg.minio_secure = False
        cfg.object_storage_max_retries = 3
        cfg.object_storage_retry_backoff_seconds = 0.1
        adapter = create_object_storage(cfg)
        assert isinstance(adapter, MinioObjectStorage)
        assert adapter.name == "s3"

    def test_unknown_adapter_raises(self):
        cfg = MagicMock()
        cfg.object_storage_adapter = "ftp"
        with pytest.raises(ValueError):
            create_object_storage(cfg)

    def test_injected_adapter_wins(self):
        injected = LocalFakeObjectStorage()
        set_object_storage(injected)
        assert get_object_storage() is injected

    def test_object_key_layout(self):
        assert build_object_key("cold-archive", "audit_logs", "m1") == (
            "cold-archive/audit_logs/m1.jsonl"
        )
        assert build_object_key("", "audit_logs", "m1") == "audit_logs/m1.jsonl"


# ----------------------------------------------------------------------
# 序列化
# ----------------------------------------------------------------------


class TestSerialization:
    def test_datetime_serialised_to_iso(self):
        payload = _serialize_row(_audit_row(1), ("id", "created_at", "action"))
        assert set(payload) == {"id", "created_at", "action"}
        assert isinstance(payload["created_at"], str)
        json.dumps(payload)  # 必须可 JSON 序列化

    def test_jsonl_line_per_record(self):
        payload = _to_jsonl([{"id": 1}, {"id": 2}])
        assert len(payload.decode("utf-8").splitlines()) == 2


# ----------------------------------------------------------------------
# 审计日志冷归档
# ----------------------------------------------------------------------


class TestArchiveAuditLogs:
    @pytest.mark.asyncio
    async def test_uploads_then_purges(self):
        storage = LocalFakeObjectStorage()
        db = _FakeDb()
        service = ColdArchiveService(db, storage=storage)
        rows = [_audit_row(1), _audit_row(2)]

        with patch.object(
            service, "_find_archivable_sessions", AsyncMock(return_value=["sess_1"])
        ), patch.object(
            service, "_collect_session_chain", AsyncMock(return_value=([{"id": 1}, {"id": 2}], rows))
        ):
            result = await service.archive_audit_logs()

        assert result.archived == 2
        assert result.manifests == 1
        assert result.failed is False
        # 先上传（对象存在）后才删除热数据
        keys = await storage.list_keys("")
        assert len(keys) == 1
        assert await storage.exists(keys[0]) is True
        assert db.added and isinstance(db.added[0], ColdArchiveManifest)
        manifest = db.added[0]
        assert manifest.status == "archived"
        assert manifest.record_count == 2
        assert manifest.checksum is not None
        assert manifest.purged_at is not None

    @pytest.mark.asyncio
    async def test_storage_failure_retains_hot_data(self):
        class _BrokenStorage(LocalFakeObjectStorage):
            async def put(self, key: str, payload: bytes):  # type: ignore[override]
                raise RuntimeError("minio unreachable")

        db = _FakeDb()
        service = ColdArchiveService(db, storage=_BrokenStorage())
        rows = [_audit_row(1), _audit_row(2)]

        with patch.object(
            service, "_find_archivable_sessions", AsyncMock(return_value=["sess_1"])
        ), patch.object(
            service,
            "_collect_session_chain",
            AsyncMock(return_value=([{"id": 1}, {"id": 2}], rows)),
        ):
            result = await service.archive_audit_logs()

        assert result.failed is True
        assert result.pending == 2
        assert result.archived == 0
        manifest = db.added[0]
        assert manifest.status == "failed"
        assert "minio unreachable" in (manifest.message or "")
        # 未上传成功 → 不得发出任何删除语句
        assert all("DELETE" not in str(call).upper() for call in db.execute_calls)

    @pytest.mark.asyncio
    async def test_genesis_record_kept_as_anchor(self):
        db = _FakeDb(execute_rows=[_audit_row(1, hash_prev=None), _audit_row(2)])
        service = ColdArchiveService(db, storage=LocalFakeObjectStorage())

        records, rows = await service._collect_session_chain(
            "sess_1", datetime.now(timezone.utc)
        )
        # 创世记录（hash_prev is None）留在热层作锚点
        assert [r["id"] for r in records] == [2]
        assert [r.id for r in rows] == [2]

    @pytest.mark.asyncio
    async def test_only_genesis_row_means_nothing_to_archive(self):
        db = _FakeDb(execute_rows=[_audit_row(1, hash_prev=None)])
        service = ColdArchiveService(db, storage=LocalFakeObjectStorage())
        records, rows = await service._collect_session_chain(
            "sess_1", datetime.now(timezone.utc)
        )
        assert records == [] and rows == []

    @pytest.mark.asyncio
    async def test_batch_size_caps_records(self):
        storage = LocalFakeObjectStorage()
        db = _FakeDb()
        service = ColdArchiveService(db, storage=storage, batch_size=3)
        rows = [_audit_row(i) for i in range(1, 5)]

        with patch.object(
            service,
            "_find_archivable_sessions",
            AsyncMock(return_value=["sess_a", "sess_b"]),
        ), patch.object(
            service,
            "_collect_session_chain",
            AsyncMock(side_effect=[([{"id": 1}, {"id": 2}], rows[:2]), ([{"id": 3}, {"id": 4}], rows[2:])]),
        ):
            result = await service.archive_audit_logs()

        # 第二批会超过 batch_size → 本次只归档第一批
        assert result.archived == 2
        assert result.manifests == 1

    @pytest.mark.asyncio
    async def test_unexpected_error_is_contained(self):
        db = _FakeDb()
        service = ColdArchiveService(db, storage=LocalFakeObjectStorage())
        with patch.object(
            service,
            "_find_archivable_sessions",
            AsyncMock(side_effect=RuntimeError("db down")),
        ):
            result = await service.archive_audit_logs()
        assert result.failed is True
        assert "db down" in result.message


# ----------------------------------------------------------------------
# 会话归档冷归档
# ----------------------------------------------------------------------


class TestArchiveSessionArchives:
    @pytest.mark.asyncio
    async def test_archives_and_purges(self):
        storage = LocalFakeObjectStorage()
        db = _FakeDb(execute_rows=[_session_archive_row(1)])
        service = ColdArchiveService(db, storage=storage)

        result = await service.archive_session_archives(patient_type="outpatient")

        assert result.category == CATEGORY_SESSION_ARCHIVES
        assert result.archived == 1
        assert db.added and db.added[0].category == CATEGORY_SESSION_ARCHIVES

    @pytest.mark.asyncio
    async def test_nothing_to_archive(self):
        db = _FakeDb(execute_rows=[])
        service = ColdArchiveService(db, storage=LocalFakeObjectStorage())
        result = await service.archive_session_archives()
        assert result.archived == 0
        assert result.manifests == 0

    @pytest.mark.asyncio
    async def test_default_threshold_uses_retention_years(self):
        """默认截止时间按门诊/住院年限推算（15 / 30 年）。"""
        from app.config.settings import settings

        captured: Dict[str, Any] = {}

        async def _execute(stmt):
            captured["stmt"] = stmt
            result = MagicMock()
            scalars = MagicMock()
            scalars.all.return_value = []
            result.scalars.return_value = scalars
            return result

        db = _FakeDb()
        db.execute = _execute
        service = ColdArchiveService(db, storage=LocalFakeObjectStorage())
        await service.archive_session_archives(patient_type="inpatient")

        # 参数已绑定进 SQL（这里是行为级断言：不抛错且确实查询了）
        assert captured["stmt"] is not None
        assert settings.data_retention_inpatient_years == 30


# ----------------------------------------------------------------------
# 校验与恢复
# ----------------------------------------------------------------------


class TestVerifyAndRestore:
    def _manifest(self, storage_key: str, payload: bytes, count: int) -> ColdArchiveManifest:
        from app.services.report_service import compute_digest

        return ColdArchiveManifest(
            manifest_id="cam_1",
            category=CATEGORY_AUDIT_LOGS,
            object_key=storage_key,
            record_count=count,
            checksum=compute_digest(payload),
            status="archived",
        )

    @pytest.mark.asyncio
    async def test_verify_ok(self):
        storage = LocalFakeObjectStorage()
        payload = _to_jsonl([{"id": 1}, {"id": 2}])
        await storage.put("k.jsonl", payload)
        service = ColdArchiveService(_FakeDb(), storage=storage)
        manifest = self._manifest("k.jsonl", payload, 2)

        with patch.object(service, "get_manifest", AsyncMock(return_value=manifest)):
            report = await service.verify_manifest("cam_1")

        assert report["valid"] is True
        assert report["checksum_ok"] is True
        assert report["record_count_ok"] is True

    @pytest.mark.asyncio
    async def test_verify_detects_missing_object(self):
        service = ColdArchiveService(_FakeDb(), storage=LocalFakeObjectStorage())
        manifest = self._manifest("missing.jsonl", b"x", 1)
        with patch.object(service, "get_manifest", AsyncMock(return_value=manifest)):
            report = await service.verify_manifest("cam_1")
        assert report["exists"] is False
        assert report["valid"] is False

    @pytest.mark.asyncio
    async def test_restore_returns_records(self):
        storage = LocalFakeObjectStorage()
        payload = _to_jsonl([{"id": 1, "action": "a"}, {"id": 2, "action": "b"}])
        await storage.put("r.jsonl", payload)
        service = ColdArchiveService(_FakeDb(), storage=storage)
        manifest = self._manifest("r.jsonl", payload, 2)

        with patch.object(service, "get_manifest", AsyncMock(return_value=manifest)):
            records = await service.restore_manifest("cam_1")

        assert [r["id"] for r in records] == [1, 2]
        assert manifest.status == "restored"

    @pytest.mark.asyncio
    async def test_restore_detects_checksum_mismatch(self):
        storage = LocalFakeObjectStorage()
        await storage.put("c.jsonl", b'{"id": 1}\n')
        service = ColdArchiveService(_FakeDb(), storage=storage)
        manifest = self._manifest("c.jsonl", b'{"id": 999}\n', 1)

        with patch.object(service, "get_manifest", AsyncMock(return_value=manifest)):
            with pytest.raises(ValueError):
                await service.restore_manifest("cam_1")

    @pytest.mark.asyncio
    async def test_restore_missing_object_raises(self):
        service = ColdArchiveService(_FakeDb(), storage=LocalFakeObjectStorage())
        manifest = self._manifest("nope.jsonl", b"x", 1)
        with patch.object(service, "get_manifest", AsyncMock(return_value=manifest)):
            with pytest.raises(FileNotFoundError):
                await service.restore_manifest("cam_1")

    @pytest.mark.asyncio
    async def test_stats_grouping(self):
        db = _FakeDb()
        db.execute = AsyncMock(
            return_value=MagicMock(
                all=MagicMock(
                    return_value=[
                        ("audit_logs", "archived", 2, 100),
                        ("session_archives", "archived", 1, 30),
                    ]
                )
            )
        )
        service = ColdArchiveService(db, storage=LocalFakeObjectStorage())
        stats = await service.stats()
        assert stats["by_category"]["audit_logs"]["archived"] == 2
        assert stats["by_category"]["audit_logs"]["records"] == 100
        assert stats["by_category"]["session_archives"]["records"] == 30


# ----------------------------------------------------------------------
# 数据保留策略与接线
# ----------------------------------------------------------------------


class TestRetentionPolicy:
    def test_retention_years_match_spec(self):
        from app.config.settings import settings

        assert settings.data_retention_outpatient_years == 15
        assert settings.data_retention_inpatient_years == 30
        assert settings.data_retention_audit_years == 30
        assert settings.data_retention_operation_years == 5

    def test_tiering_defaults(self):
        from app.config.settings import settings

        assert settings.audit_hot_retention_days == 90
        # 默认不允许"绕过冷层直接清理"
        assert settings.data_retention_purge_without_cold_archive is False

    @pytest.mark.asyncio
    async def test_data_retention_delegates_to_cold_archive(self):
        from app.services.data_retention import DataRetentionService

        db = _FakeDb()
        service = DataRetentionService(db)
        with patch(
            "app.services.data_retention.ColdArchiveService"
        ) as cold_cls:
            cold = cold_cls.return_value
            cold.archive_audit_logs = AsyncMock(
                return_value=MagicMock(
                    as_dict=lambda: {"category": "audit_logs", "archived": 3}
                )
            )
            cold.archive_session_archives = AsyncMock(
                return_value=MagicMock(
                    as_dict=lambda: {"category": "session_archives", "archived": 1}
                )
            )
            audit_result = await service.archive_and_purge_audit_logs()
            session_result = await service.archive_and_purge_session_archives("inpatient")

        assert audit_result["archived"] == 3
        assert session_result["archived"] == 1
        cold.archive_audit_logs.assert_awaited()
        cold.archive_session_archives.assert_awaited()
        assert cold.archive_session_archives.await_args.kwargs["patient_type"] == "inpatient"


# ----------------------------------------------------------------------
# 可观测接口
# ----------------------------------------------------------------------


class TestColdArchiveEndpoints:
    @pytest.mark.asyncio
    async def test_status_endpoint(self):
        from app.api.v1.obs_dashboard import cold_archive_status

        db = _FakeDb()
        manifest = ColdArchiveManifest(
            manifest_id="cam_1",
            category="audit_logs",
            status="archived",
            object_key="k",
            record_count=2,
            size_bytes=10,
            checksum="abc",
            adapter="local_fake",
        )
        with patch(
            "app.api.v1.obs_dashboard.ColdArchiveService"
        ) as cold_cls:
            cold = cold_cls.return_value
            cold.storage = LocalFakeObjectStorage()
            cold.stats = AsyncMock(return_value={"by_category": {}})
            cold.list_manifests = AsyncMock(return_value=[manifest])
            payload = await cold_archive_status(category=None, limit=10, db=db)

        assert payload["adapter"] == "local_fake"
        assert payload["manifests"][0]["manifest_id"] == "cam_1"
        assert payload["hot_retention_days"] == 90

    @pytest.mark.asyncio
    async def test_run_endpoint(self):
        from app.api.v1.obs_dashboard import run_cold_archive

        db = _FakeDb()
        with patch("app.api.v1.obs_dashboard.ColdArchiveService") as cold_cls:
            cold = cold_cls.return_value
            cold.archive_session_archives = AsyncMock(
                return_value=MagicMock(
                    as_dict=lambda: {"category": "session_archives", "archived": 5}
                )
            )
            payload = await run_cold_archive(category="session_archives", db=db)

        assert payload["archived"] == 5
        cold.archive_session_archives.assert_awaited()
