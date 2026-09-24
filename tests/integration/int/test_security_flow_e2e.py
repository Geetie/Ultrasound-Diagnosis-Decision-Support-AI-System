"""T-GB-INT-03 安全合规全链路：三层脱敏 + 同意 + 审计 + HSM + 冷归档。

链路：**临床文本（含 PII）→ 脱敏 → 诊断/报告 → HSM 数字签名 → 审计留痕
（可校验哈希链）→ 冷归档（热层保留创世锚点，全链可从冷层恢复）**。

与 ``tests/unit/sec/test_e2e_security_flow.py`` 的差别：那里逐组件验证，
本文件按"一次真实业务动作"串联，专门锁定跨模块的契约：

- 报告正文中的 PII 必须已被脱敏（否则报告会把隐私写进冷层与 PACS）；
- 同意撤回后**不得**生成报告（同意闸门在报告路径上同样生效）；
- 报告签名用真实 RSA-PSS（轻量 HSM，无 SoftHSM 时间依赖），正文被改动即验签失败；
- 审计动作按会话形成可校验哈希链；
- 冷归档后热层保留创世记录，**创世 + 冷层记录**拼回后整链仍然自洽。
"""
from __future__ import annotations

import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.errors import PatientConsentWithdrawnException
from app.integrations.object_storage import LocalFakeObjectStorage
from app.models.database import AuditLog, DiagnosisReport, DiagnosisResult
from app.models.schemas import ConsentStatus, ReportDraftRequest
from app.observability.audit_chain.verify_utils import verify_chain_integrity
from app.security.desensitization import DesensitizationEngine
from app.services import report_service
from app.services.audit_service import AuditService
from app.services.cold_archive import ColdArchiveService

PII_CLINICAL_TEXT = (
    "患者张三，身份证 110101199001011234，手机 13812345678；"
    "甲状腺右叶低回声结节，边界不清，微钙化。"
)


# ----------------------------------------------------------------------
# 桩件
# ----------------------------------------------------------------------


class _MemoryDb:
    """内存审计库（单会话链 + 冷归档清单），支持 select/delete。"""

    def __init__(self) -> None:
        self.rows: list[AuditLog] = []
        self.manifests: list[Any] = []

    @staticmethod
    def _entity(stmt):
        try:
            return stmt.column_descriptions[0].get("entity")
        except Exception:
            return None

    async def execute(self, stmt):
        from sqlalchemy.sql.dml import Delete

        if isinstance(stmt, Delete):
            params = stmt.compile().params
            ids: set = set()
            for key, value in params.items():
                if not key.startswith("id"):
                    continue
                # in_() 使用 POSTCOMPILE 展开参数，值是列表
                if isinstance(value, (list, tuple, set)):
                    ids.update(v for v in value if isinstance(v, int))
                elif isinstance(value, int):
                    ids.add(value)
            before = len(self.rows)
            self.rows = [row for row in self.rows if row.id not in ids]
            result = MagicMock()
            result.rowcount = before - len(self.rows)
            return result

        result = MagicMock()
        entity = self._entity(stmt)
        sql = str(stmt)

        if "GROUP BY" in sql.upper():
            # 冷归档的"整条链已超期"聚合查询（须先判，select(AuditLog.session_id)
            # 的 column_descriptions 里 entity 仍可能是 AuditLog）
            sessions = sorted({row.session_id for row in self.rows if row.session_id})
            scalars = MagicMock()
            scalars.all.return_value = sessions
            result.scalars.return_value = scalars
        elif entity is AuditLog:
            scalars = MagicMock()
            scalars.all.return_value = list(self.rows)
            result.scalars.return_value = scalars
            result.scalar_one_or_none.return_value = self.rows[-1] if self.rows else None
        elif entity is not None and entity.__name__ == "ColdArchiveManifest":
            scalars = MagicMock()
            scalars.all.return_value = list(self.manifests)
            result.scalars.return_value = scalars
            result.scalar_one_or_none.return_value = (
                self.manifests[-1] if self.manifests else None
            )
        else:
            scalars = MagicMock()
            scalars.all.return_value = []
            result.scalars.return_value = scalars
            result.scalar_one_or_none.return_value = None

        result.all.return_value = []
        return result

    def add(self, row) -> None:
        if row.__class__.__name__ == "ColdArchiveManifest":
            self.manifests.append(row)
            return
        # 模拟自增主键，供冷归档按 id 删除
        if getattr(row, "id", None) is None:
            row.id = len(self.rows) + 1
        self.rows.append(row)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


def _make_lightweight_hsm():
    """构造不依赖 Redis / SoftHSM 的真实 RSA 密钥管理实例。"""
    from app.security.hsm import HSMManager, KeySlot

    mgr = HSMManager.__new__(HSMManager)
    mgr._use_soft_hsm = False
    mgr._hsm_available = False
    mgr._key_slots = {slot: {} for slot in KeySlot}
    mgr._lock = threading.RLock()
    mgr._is_degraded = False
    mgr._degraded_at = None
    mgr._degraded_reason = None
    mgr._soft_hsm_enabled_at = None
    mgr._key_cleanup_timer = None
    mgr._periodic_check_timer = None
    mgr._pending_tasks = set()
    mgr._loop = None
    return mgr


class _FakeReportRepo:
    def __init__(self) -> None:
        self.reports: dict[str, DiagnosisReport] = {}
        self.db = _MemoryDb()

    async def get_latest_by_request(self, request_id: str):
        for report in self.reports.values():
            if report.request_id == request_id:
                return report
        return None

    async def get_report(self, report_id: str):
        return self.reports.get(report_id)

    async def create_report(self, **kwargs) -> DiagnosisReport:
        report = DiagnosisReport(
            report_id=kwargs.get("report_id") or "rep_sec_e2e",
            request_id=kwargs["request_id"],
            session_id=kwargs.get("session_id"),
            patient_id=kwargs.get("patient_id"),
            organ=kwargs.get("organ"),
            status="draft",
            content=kwargs.get("content"),
            rendered=kwargs.get("rendered"),
            content_digest=kwargs.get("content_digest"),
            template_version=kwargs.get("template_version"),
            pacs_status="pending",
            pacs_attempts=0,
        )
        self.reports[report.report_id] = report
        return report

    async def mark_signed(self, report_id, *, signature, signing_key_name, signed_by=None):
        report = self.reports[report_id]
        report.signature = signature
        report.signing_key_name = signing_key_name
        report.signed_by = signed_by
        report.status = "signed"
        return report

    async def mark_pacs_result(
        self, report_id, *, adapter=None, success=False, message="", attempts=1, skipped=False
    ):
        report = self.reports[report_id]
        if skipped:
            report.pacs_status = "skipped"
        else:
            report.pacs_status = "success" if success else "failed"
        report.pacs_message = message
        return report


class _ConsentRepo:
    def __init__(self, status: str) -> None:
        self._status = status

    async def get_latest_consent(self, patient_id):
        return MagicMock(status=self._status)


class _SessionRepo:
    async def get_session(self, session_id):
        session = MagicMock()
        session.session_id = session_id
        session.patient_id = "P001"
        session.knowledge_base_version = "kb_v1.2.0"
        return session

    async def renew_session(self, session_id):
        return None


class _DiagnosisRepo:
    def __init__(self, result) -> None:
        self._result = result

    async def get_result(self, request_id):
        return self._result


def _diagnosis_row() -> DiagnosisResult:
    return DiagnosisResult(
        request_id="req_sec_e2e",
        session_id="sess_sec_e2e",
        organ="thyroid",
        tier="standard",
        clinical_text=PII_CLINICAL_TEXT,
        confidence=0.9,
        findings={"fusion_result": {"needs_review": False}},
        recommendations="TI-RADS 4a，建议细针穿刺活检。",
        knowledge_ids=["kb_1"],
        status="completed",
    )


@pytest.fixture(autouse=True)
def _no_hsm_redis():
    """屏蔽 HSM 的 Redis 持久化与 desensitization 的 Redis 交互。"""
    from app.security.hsm import HSMManager
    from app.security.hsm.persistence_manager import PersistenceManager

    def _noop_safe_run(self, coro):
        coro.close()

    with patch.object(HSMManager, "_safe_run_async_task", _noop_safe_run), patch.object(
        PersistenceManager, "safe_run_async_task", _noop_safe_run
    ), patch.object(
        DesensitizationEngine, "load_state_from_redis", AsyncMock()
    ), patch.object(
        DesensitizationEngine, "_persist_token_to_redis"
    ), patch.object(
        DesensitizationEngine, "_publish_rules_update"
    ), patch.object(
        DesensitizationEngine, "subscribe_rules_updates", AsyncMock()
    ):
        DesensitizationEngine._instance = None
        yield
        DesensitizationEngine._instance = None


# ----------------------------------------------------------------------
# 1) 脱敏 → 报告内容
# ----------------------------------------------------------------------


class TestDesensitizationBeforeReport:
    def test_clinical_text_pii_is_removed_before_report(self):
        engine = DesensitizationEngine()
        engine.load_rules()

        sanitized = engine.desensitize(PII_CLINICAL_TEXT)

        assert "110101199001011234" not in sanitized
        assert "13812345678" not in sanitized
        assert "张三" not in sanitized
        # 临床信息本身必须保留（脱敏只处理 PII）
        assert "低回声结节" in sanitized
        assert "微钙化" in sanitized

    def test_report_content_uses_desensitized_findings(self):
        engine = DesensitizationEngine()
        engine.load_rules()

        result = _diagnosis_row()
        result.clinical_text = engine.desensitize(PII_CLINICAL_TEXT)
        content = report_service.build_report_content(result)

        assert "110101199001011234" not in content["findings"]
        assert "13812345678" not in content["findings"]

    def test_report_content_marks_pii_when_not_desensitized(self):
        """反向锁定：未脱敏的文本会原样进入报告 → 提醒脱敏必须在入库前完成。"""
        content = report_service.build_report_content(_diagnosis_row())
        assert "110101199001011234" in content["findings"]


# ----------------------------------------------------------------------
# 2) 同意闸门（报告路径）
# ----------------------------------------------------------------------


class TestConsentGateOnReport:
    @pytest.mark.asyncio
    async def test_withdrawn_consent_blocks_report_generation(self):
        from app.api.v1.report import create_report_draft

        with pytest.raises(PatientConsentWithdrawnException):
            await create_report_draft(
                payload=ReportDraftRequest(request_id="req_sec_e2e"),
                report_repo=_FakeReportRepo(),
                diagnosis_repo=_DiagnosisRepo(_diagnosis_row()),
                session_repo=_SessionRepo(),
                consent_repo=_ConsentRepo(ConsentStatus.WITHDRAWN.value),
                audit_service=MagicMock(log_action=AsyncMock()),
                current_user={"user_id": "doc_1"},
            )

    @pytest.mark.asyncio
    async def test_granted_consent_allows_report_generation(self):
        from app.api.v1.report import create_report_draft

        repo = _FakeReportRepo()
        audit = MagicMock(log_action=AsyncMock())
        view = await create_report_draft(
            payload=ReportDraftRequest(request_id="req_sec_e2e"),
            report_repo=repo,
            diagnosis_repo=_DiagnosisRepo(_diagnosis_row()),
            session_repo=_SessionRepo(),
            consent_repo=_ConsentRepo(ConsentStatus.GRANTED.value),
            audit_service=audit,
            current_user={"user_id": "doc_1"},
        )

        assert view.status == "draft"
        audit.log_action.assert_awaited()
        assert audit.log_action.await_args.kwargs["action"] == "report_draft"


# ----------------------------------------------------------------------
# 3) HSM 签名 + 4) 审计链 + 5) 冷归档锚点
# ----------------------------------------------------------------------


class TestSignedReportWithAuditChain:
    @pytest.mark.asyncio
    async def test_full_flow_signature_and_chain_recoverable_from_cold(self):
        hsm = _make_lightweight_hsm()
        repo = _FakeReportRepo()
        db = repo.db
        audit = AuditService(db)

        with patch("app.security.hsm.hsm_manager", hsm):
            # 3a) 签发：真实 RSA-PSS
            report = await report_service.generate_report_draft(
                result=_diagnosis_row(), report_repo=repo, session=_SessionRepo
            )
            report = await report_service.sign_report(
                report=report, report_repo=repo, actor_id="doc_1"
            )
            assert report.signature
            assert report_service.verify_report_signature(report) is True

            # 3b) 正文被改动 → 验签失败
            original = report.rendered
            report.rendered = original + "\n事后追加"
            assert report_service.verify_report_signature(report) is False
            report.rendered = original

            # 3c) 审计留痕：草稿 / 签发 / 推送
            for action in ("report_draft", "report_sign", "report_push"):
                await audit.log_action(
                    action=action,
                    actor="doc_1",
                    session_id="sess_sec_e2e",
                    target_type="report",
                    target_id=report.report_id,
                    detail={"content_digest": report.content_digest},
                )

        # 4) 审计链自洽
        assert len(db.rows) == 3
        valid, broken, _expected, actual = verify_chain_integrity(db.rows)
        assert valid is True, f"审计链断裂于 {broken}（hash_prev={actual}）"
        assert db.rows[0].hash_prev is None
        assert db.rows[1].hash_prev == db.rows[0].hash_self
        assert db.rows[2].hash_prev == db.rows[1].hash_self

        # 5) 冷归档：热层只留创世锚点，全链仍可从冷层恢复
        storage = LocalFakeObjectStorage()
        archiver = ColdArchiveService(db, storage=storage)
        result = await archiver.archive_audit_logs()

        assert result.archived == 2
        assert result.failed is False

        # 热层只留创世记录（归档动作本身另记一条 session_id=None 的全局审计）
        hot_chain = [row for row in db.rows if row.session_id == "sess_sec_e2e"]
        assert len(hot_chain) == 1
        assert hot_chain[0].hash_prev is None
        # 热层剩余部分仍是合法链（否则合规校验会立刻报"审计链断裂"）
        assert verify_chain_integrity(hot_chain)[0] is True
        assert any(row.action == "audit_logs_cold_archived" for row in db.rows)

        # 冷层写住了，且可校验
        manifest_id = result.manifest_ids[0]
        verify_report = await archiver.verify_manifest(manifest_id)
        assert verify_report["valid"] is True

        # 创世 + 冷层记录 = 完整链
        archived_records = await archiver.restore_manifest(manifest_id)
        assert [r["action"] for r in archived_records] == ["report_sign", "report_push"]
        assert archived_records[0]["hash_prev"] == hot_chain[0].hash_self
        assert archived_records[1]["hash_prev"] == archived_records[0]["hash_self"]

    @pytest.mark.asyncio
    async def test_unsigned_report_never_reaches_pacs(self):
        from app.integrations.pacs import LocalFakePacsAdapter

        repo = _FakeReportRepo()
        adapter = LocalFakePacsAdapter()
        report = await report_service.generate_report_draft(
            result=_diagnosis_row(), report_repo=repo, session=_SessionRepo()
        )

        push_result = await report_service.push_report(
            report=report, report_repo=repo, adapter=adapter
        )

        assert push_result.skipped is True
        assert adapter.pushes == []
        assert report.pacs_status == "skipped"

    @pytest.mark.asyncio
    async def test_key_slot_isolation_for_report_signing(self):
        """报告签名密钥与 JWT / 应急签名密钥必须隔离。"""
        from app.security.hsm import KeySlot

        hsm = _make_lightweight_hsm()
        repo = _FakeReportRepo()
        with patch("app.security.hsm.hsm_manager", hsm):
            report = await report_service.generate_report_draft(
                result=_diagnosis_row(), report_repo=repo, session=_SessionRepo()
            )
            await report_service.sign_report(report=report, report_repo=repo)

        assert hsm._get_key_slot("report_signing_v1") == KeySlot.REPORT_SIGNING
        keys = hsm.list_keys_by_slot(KeySlot.REPORT_SIGNING)
        assert "report_signing_v1" in keys
        assert "report_signing_v1" not in hsm.list_keys_by_slot(KeySlot.JWT_SIGNING)
