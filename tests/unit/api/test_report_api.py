"""T-GB-API-02 / T-GB-API-01 报告生成签发与 FHIR CRUD 测试。

覆盖：
- 报告内容构建、模板渲染、摘要计算；
- 草稿生成（含复用既有草稿）；
- HSM 签名（含按需生成 report_ 前缀密钥）与签名校验（正文被改动即失败）；
- PACS 推送（未签名拒绝推送、成功/失败回写、适配器异常降级）；
- PACS 适配器工厂（local_fake / http / 未知名报错）；
- 报告接口：草稿 / 签名（重复签名拒绝）/ 推送（未签名拒绝）/ 校验；
- FHIR Patient 增改删查与派生资源检索；
- 路由注册冒烟（避免"写了没挂"）。
"""
from __future__ import annotations

import base64
import hashlib
from datetime import date
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.pacs import (
    HttpPacsAdapter,
    LocalFakePacsAdapter,
    create_pacs_adapter,
    set_pacs_adapter,
)
from app.models.database import DiagnosisReport, DiagnosisResult
from app.models.schemas import ConsentStatus
from app.services import report_service


# ----------------------------------------------------------------------
# 桩件
# ----------------------------------------------------------------------


class _FakeHSM:
    """最小 HSM 替身：签名 = b"sig:" + sha256(data)（对整段载荷敏感）。"""

    def __init__(self) -> None:
        self.keys: Dict[str, Any] = {}
        self.sign_calls: List[Any] = []

    def get_key_info(self, key_name: str):
        return self.keys.get(key_name)

    def generate_key_pair(self, key_name, key_size=2048, slot=None):
        self.keys[key_name] = {"slot": getattr(slot, "value", slot), "size": key_size}
        return {"key_name": key_name}

    def sign(self, key_name: str, data: bytes) -> bytes:
        self.sign_calls.append((key_name, data))
        return b"sig:" + hashlib.sha256(data).digest()

    def verify_signature(self, key_name: str, data: bytes, signature: bytes) -> bool:
        return signature == b"sig:" + hashlib.sha256(data).digest()


def _diagnosis_result(**overrides) -> DiagnosisResult:
    row = DiagnosisResult(
        request_id="req_1",
        session_id="sess_1",
        organ="thyroid",
        tier="standard",
        clinical_text="甲状腺右叶低回声结节，边界不清",
        confidence=0.88,
        findings={
            "organ": "thyroid",
            "fusion_result": {"needs_review": False, "review_reason": None},
            "timings_ms": {"pipeline.total_ms": 1234.5},
        },
        recommendations="TI-RADS 4a，建议细针穿刺活检。",
        knowledge_ids=["kb_1", "kb_2"],
        status="completed",
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _session() -> MagicMock:
    session = MagicMock()
    session.session_id = "sess_1"
    session.patient_id = "P001"
    session.knowledge_base_version = "kb_v1.2.0"
    return session


class _FakeReportRepo:
    """内存报告仓储替身。"""

    def __init__(self) -> None:
        self.reports: Dict[str, DiagnosisReport] = {}
        self._seq = 0
        self.db = MagicMock()
        self.db.commit = AsyncMock()

    async def get_report(self, report_id: str):
        return self.reports.get(report_id)

    async def get_latest_by_request(self, request_id: str):
        for report in self.reports.values():
            if report.request_id == request_id:
                return report
        return None

    async def create_report(self, **kwargs) -> DiagnosisReport:
        self._seq += 1
        report = DiagnosisReport(
            report_id=kwargs.get("report_id") or f"rep_test_{self._seq}",
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
        report = self.reports.get(report_id)
        if report is None:
            return None
        report.signature = signature
        report.signing_key_name = signing_key_name
        report.signed_by = signed_by
        report.status = "signed"
        return report

    async def mark_pacs_result(
        self, report_id, *, adapter, success, message="", attempts=1, skipped=False
    ):
        report = self.reports.get(report_id)
        if report is None:
            return None
        report.pacs_adapter = adapter
        report.pacs_attempts = (report.pacs_attempts or 0) + max(1, attempts)
        report.pacs_status = "skipped" if skipped else ("success" if success else "failed")
        report.pacs_message = message
        if success and not skipped:
            report.status = "pushed"
        return report


# ----------------------------------------------------------------------
# 内容与模板
# ----------------------------------------------------------------------


class TestReportContent:
    def test_content_fields(self):
        content = report_service.build_report_content(_diagnosis_result(), _session())
        assert content["organ_label"] == "甲状腺"
        assert content["patient_id"] == "P001"
        assert content["knowledge_base_version"] == "kb_v1.2.0"
        assert "低回声结节" in content["findings"]
        assert "TI-RADS 4a" in content["diagnosis"]
        assert content["evidence"] == "kb_1、kb_2"
        assert content["review_required"] == "否"
        assert content["template_version"] == report_service.TEMPLATE_VERSION

    def test_needs_review_marked(self):
        result = _diagnosis_result(
            findings={
                "fusion_result": {"needs_review": True, "review_reason": "low_confidence"},
            }
        )
        content = report_service.build_report_content(result)
        assert content["review_required"] == "是"
        assert content["consistency_status"] == "需人工复核"
        assert content["review_reason"] == "low_confidence"

    def test_missing_clinical_text_and_conclusion_have_placeholders(self):
        result = _diagnosis_result(clinical_text=None, recommendations=None)
        content = report_service.build_report_content(result)
        assert content["findings"].startswith("（")
        assert content["diagnosis"].startswith("（")

    def test_render_leaves_no_placeholder(self):
        content = report_service.build_report_content(_diagnosis_result(), _session())
        rendered = report_service.render_template(
            "{{report_id}}|{{organ_label}}|{{missing_key}}", content
        )
        assert "{{" not in rendered
        assert "甲状腺" in rendered

    def test_default_template_file_loads(self):
        template = report_service.load_template("default.md")
        assert "超声诊断报告" in template
        assert "{{diagnosis}}" in template

    def test_missing_template_falls_back(self):
        template = report_service.load_template("not-exists.md")
        assert "超声诊断报告" in template

    def test_digest_is_sha256_of_payload(self):
        payload = report_service.report_payload("abc")
        assert report_service.compute_digest(payload) == hashlib.sha256(b"abc").hexdigest()


# ----------------------------------------------------------------------
# 草稿
# ----------------------------------------------------------------------


class TestReportDraft:
    @pytest.mark.asyncio
    async def test_generate_draft_persists_digest_and_rendered(self):
        repo = _FakeReportRepo()
        report = await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        assert report.status == "draft"
        assert report.rendered and "甲状腺" in report.rendered
        assert report.content_digest == hashlib.sha256(
            report.rendered.encode("utf-8")
        ).hexdigest()
        assert report.pacs_status == "pending"

    @pytest.mark.asyncio
    async def test_generate_draft_reuses_existing(self):
        repo = _FakeReportRepo()
        first = await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        second = await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        assert first.report_id == second.report_id
        assert len(repo.reports) == 1

    @pytest.mark.asyncio
    async def test_draft_generation_is_fast(self):
        """规格：报告草稿 ≤ 2 分钟。纯内存操作应远低于预算。"""
        import time

        repo = _FakeReportRepo()
        started = time.monotonic()
        await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        assert time.monotonic() - started < 5.0


# ----------------------------------------------------------------------
# 签名
# ----------------------------------------------------------------------


class TestReportSigning:
    @pytest.mark.asyncio
    async def test_sign_report_uses_hsm_and_stores_base64(self):
        repo = _FakeReportRepo()
        report = await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        hsm = _FakeHSM()

        with patch("app.security.hsm.hsm_manager", hsm):
            signed = await report_service.sign_report(
                report=report, report_repo=repo, actor_id="doc_1"
            )

        assert signed.status == "signed"
        assert signed.signing_key_name == "report_signing_v1"
        assert base64.b64decode(signed.signature) == b"sig:" + hashlib.sha256(
            report.rendered.encode("utf-8")
        ).digest()
        assert hsm.sign_calls and hsm.sign_calls[0][1] == report.rendered.encode("utf-8")
        assert signed.signed_by == "doc_1"

    @pytest.mark.asyncio
    async def test_signing_key_created_with_report_slot(self):
        repo = _FakeReportRepo()
        report = await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        hsm = _FakeHSM()

        with patch("app.security.hsm.hsm_manager", hsm):
            await report_service.sign_report(report=report, report_repo=repo)

        assert hsm.keys["report_signing_v1"]["slot"] == "report_signing"

    @pytest.mark.asyncio
    async def test_verify_signature_ok_and_tampered(self):
        repo = _FakeReportRepo()
        report = await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        hsm = _FakeHSM()
        with patch("app.security.hsm.hsm_manager", hsm):
            await report_service.sign_report(report=report, report_repo=repo)
            assert report_service.verify_report_signature(report) is True

            report.rendered = report.rendered + "\n被篡改"
            assert report_service.verify_report_signature(report) is False

    def test_verify_returns_false_without_signature(self):
        report = DiagnosisReport(report_id="r", request_id="q", status="draft", rendered="x")
        assert report_service.verify_report_signature(report) is False

    def test_key_slot_routing_prefers_report_slot(self):
        from app.security.hsm.enums import KeySlot, get_key_slot

        assert get_key_slot("report_signing_v1") == KeySlot.REPORT_SIGNING
        assert KeySlot.REPORT_SIGNING.value == "report_signing"


# ----------------------------------------------------------------------
# 推送
# ----------------------------------------------------------------------


class TestReportPush:
    @pytest.mark.asyncio
    async def test_unsigned_report_is_not_pushed(self):
        repo = _FakeReportRepo()
        report = await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        adapter = LocalFakePacsAdapter()

        result = await report_service.push_report(
            report=report, report_repo=repo, adapter=adapter
        )
        assert result.skipped is True
        assert adapter.pushes == []
        assert report.pacs_status == "skipped"

    @pytest.mark.asyncio
    async def test_signed_report_pushed_successfully(self):
        repo = _FakeReportRepo()
        report = await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        hsm = _FakeHSM()
        adapter = LocalFakePacsAdapter()
        with patch("app.security.hsm.hsm_manager", hsm):
            await report_service.sign_report(report=report, report_repo=repo)
            result = await report_service.push_report(
                report=report, report_repo=repo, adapter=adapter
            )

        assert result.success is True
        assert report.pacs_status == "success"
        assert report.status == "pushed"
        assert adapter.pushes[0]["idempotency_key"].startswith(report.report_id)
        assert adapter.pushes[0]["payload"] == report.rendered.encode("utf-8")

    @pytest.mark.asyncio
    async def test_push_failure_keeps_report_signed(self):
        repo = _FakeReportRepo()
        report = await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        hsm = _FakeHSM()
        adapter = LocalFakePacsAdapter(fail_times=1)
        with patch("app.security.hsm.hsm_manager", hsm):
            await report_service.sign_report(report=report, report_repo=repo)
            result = await report_service.push_report(
                report=report, report_repo=repo, adapter=adapter
            )

        assert result.success is False
        assert report.status == "signed"
        assert report.pacs_status == "failed"
        assert report.pacs_message == "simulated failure"

    @pytest.mark.asyncio
    async def test_adapter_exception_is_contained(self):
        repo = _FakeReportRepo()
        report = await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        hsm = _FakeHSM()

        class _Boom:
            name = "boom"

            async def push_report(self, **kwargs):
                raise RuntimeError("pacs down")

            async def health(self) -> bool:
                return False

        with patch("app.security.hsm.hsm_manager", hsm):
            await report_service.sign_report(report=report, report_repo=repo)
            result = await report_service.push_report(
                report=report, report_repo=repo, adapter=_Boom()
            )

        assert result.success is False
        assert "pacs down" in result.message
        assert report.pacs_status == "failed"


class TestPacsAdapterFactory:
    def teardown_method(self):
        set_pacs_adapter(None)

    def test_local_fake(self):
        settings_obj = MagicMock()
        settings_obj.pacs_adapter = "local_fake"
        assert isinstance(create_pacs_adapter(settings_obj), LocalFakePacsAdapter)

    def test_http_requires_endpoint(self):
        settings_obj = MagicMock()
        settings_obj.pacs_adapter = "http"
        settings_obj.pacs_endpoint = ""
        with pytest.raises(ValueError):
            create_pacs_adapter(settings_obj)

    def test_http_builds_adapter(self):
        settings_obj = MagicMock()
        settings_obj.pacs_adapter = "http"
        settings_obj.pacs_endpoint = "http://pacs.local/report"
        settings_obj.pacs_timeout_seconds = 5.0
        settings_obj.pacs_max_attempts = 2
        settings_obj.pacs_retry_backoff_seconds = 0.0
        adapter = create_pacs_adapter(settings_obj)
        assert isinstance(adapter, HttpPacsAdapter)
        assert adapter.name == "http"

    def test_unknown_adapter_raises(self):
        settings_obj = MagicMock()
        settings_obj.pacs_adapter = "dicom-mwl"
        with pytest.raises(ValueError):
            create_pacs_adapter(settings_obj)

    @pytest.mark.asyncio
    async def test_http_adapter_retries_then_succeeds(self, monkeypatch):
        attempts: List[int] = []

        class _Resp:
            def __init__(self, code: int) -> None:
                self.status_code = code

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, *args, **kwargs):
                attempts.append(1)
                return _Resp(500 if len(attempts) < 2 else 200)

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        adapter = HttpPacsAdapter(
            "http://pacs.local/report", max_attempts=3, backoff_seconds=0.0
        )
        result = await adapter.push_report(
            report_id="r1", payload=b"x", idempotency_key="k1"
        )
        assert result.success is True
        assert result.attempts == 2

    @pytest.mark.asyncio
    async def test_http_adapter_does_not_retry_4xx(self, monkeypatch):
        attempts: List[int] = []

        class _Resp:
            status_code = 422

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, *args, **kwargs):
                attempts.append(1)
                return _Resp()

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        adapter = HttpPacsAdapter(
            "http://pacs.local/report", max_attempts=3, backoff_seconds=0.0
        )
        result = await adapter.push_report(
            report_id="r1", payload=b"x", idempotency_key="k1"
        )
        assert result.success is False
        assert result.attempts == 1
        assert len(attempts) == 1


# ----------------------------------------------------------------------
# 接口
# ----------------------------------------------------------------------


class _ConsentRepo:
    def __init__(self) -> None:
        self.record = MagicMock(status=ConsentStatus.GRANTED.value)

    async def get_latest_consent(self, patient_id):
        return self.record


class _SessionRepo:
    def __init__(self, session) -> None:
        self._session = session

    async def get_session(self, session_id):
        return self._session

    async def renew_session(self, session_id):
        return self._session


class _DiagnosisRepo:
    def __init__(self, result) -> None:
        self._result = result

    async def get_result(self, request_id):
        return self._result


class _Audit:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def log_action(self, **kwargs):
        self.calls.append(kwargs)


class TestReportEndpoints:
    @pytest.mark.asyncio
    async def test_draft_endpoint(self):
        from app.api.v1.report import create_report_draft
        from app.models.schemas import ReportDraftRequest

        repo = _FakeReportRepo()
        audit = _Audit()
        resp = await create_report_draft(
            payload=ReportDraftRequest(request_id="req_1"),
            report_repo=repo,
            diagnosis_repo=_DiagnosisRepo(_diagnosis_result()),
            session_repo=_SessionRepo(_session()),
            consent_repo=_ConsentRepo(),
            audit_service=audit,
            current_user={"user_id": "doc_1"},
        )
        assert resp.status == "draft"
        assert resp.signature_present is False
        assert audit.calls[0]["action"] == "report_draft"

    @pytest.mark.asyncio
    async def test_draft_with_sign_and_push(self):
        from app.api.v1.report import create_report_draft
        from app.models.schemas import ReportDraftRequest

        repo = _FakeReportRepo()
        audit = _Audit()
        adapter = LocalFakePacsAdapter()
        hsm = _FakeHSM()
        set_pacs_adapter(adapter)
        try:
            with patch("app.security.hsm.hsm_manager", hsm):
                resp = await create_report_draft(
                    payload=ReportDraftRequest(request_id="req_1", sign=True, push=True),
                    report_repo=repo,
                    diagnosis_repo=_DiagnosisRepo(_diagnosis_result()),
                    session_repo=_SessionRepo(_session()),
                    consent_repo=_ConsentRepo(),
                    audit_service=audit,
                    current_user={"user_id": "doc_1"},
                )
        finally:
            set_pacs_adapter(None)

        assert resp.status == "pushed"
        assert resp.signature_present is True
        assert resp.signature_verified is True
        assert resp.pacs_status == "success"
        actions = [call["action"] for call in audit.calls]
        assert actions == ["report_draft", "report_sign", "report_push"]

    @pytest.mark.asyncio
    async def test_sign_endpoint_rejects_double_sign(self):
        from app.api.v1.errors import BadRequestException
        from app.api.v1.report import sign_report
        from app.models.schemas import ReportSignRequest

        repo = _FakeReportRepo()
        report = await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        hsm = _FakeHSM()
        with patch("app.security.hsm.hsm_manager", hsm):
            await sign_report(
                report_id=report.report_id,
                payload=ReportSignRequest(),
                report_repo=repo,
                audit_service=_Audit(),
                current_user=None,
            )
            with pytest.raises(BadRequestException):
                await sign_report(
                    report_id=report.report_id,
                    payload=ReportSignRequest(),
                    report_repo=repo,
                    audit_service=_Audit(),
                    current_user=None,
                )

    @pytest.mark.asyncio
    async def test_push_endpoint_rejects_unsigned(self):
        from app.api.v1.errors import BadRequestException
        from app.api.v1.report import push_report
        from app.models.schemas import ReportPushRequest

        repo = _FakeReportRepo()
        report = await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        with pytest.raises(BadRequestException):
            await push_report(
                report_id=report.report_id,
                payload=ReportPushRequest(),
                report_repo=repo,
                audit_service=_Audit(),
                current_user=None,
            )

    @pytest.mark.asyncio
    async def test_get_and_verify_endpoints(self):
        from app.api.v1.report import get_report, verify_signature

        repo = _FakeReportRepo()
        report = await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        hsm = _FakeHSM()
        with patch("app.security.hsm.hsm_manager", hsm):
            await report_service.sign_report(report=report, report_repo=repo)
            view = await get_report(
                report_id=report.report_id,
                verify=True,
                report_repo=repo,
                current_user=None,
            )
            checked = await verify_signature(
                report_id=report.report_id, report_repo=repo, current_user=None
            )

        assert view.signature_verified is True
        assert checked["signature_verified"] is True

    @pytest.mark.asyncio
    async def test_get_by_request_endpoint(self):
        from app.api.v1.report import get_report_by_request

        repo = _FakeReportRepo()
        await report_service.generate_report_draft(
            result=_diagnosis_result(), report_repo=repo, session=_session()
        )
        view = await get_report_by_request(
            request_id="req_1", report_repo=repo, current_user=None
        )
        assert view.request_id == "req_1"


# ----------------------------------------------------------------------
# 路由注册冒烟
# ----------------------------------------------------------------------


class TestRouteRegistration:
    def test_report_and_fhir_routes_registered(self):
        from app.main import app

        paths = {route.path for route in app.routes}
        assert "/api/v1/report" in paths
        assert "/api/v1/report/{report_id}/sign" in paths
        assert "/api/v1/report/{report_id}/push" in paths
        assert "/fhir/Patient" in paths
        assert "/fhir/Patient/{id}" in paths
        assert "/fhir/ImagingStudy/{id}" in paths
        assert "/fhir/Observation/{id}" in paths


# ----------------------------------------------------------------------
# FHIR Patient CRUD（派生资源检索）
# ----------------------------------------------------------------------


class _FakeDb:
    """最小 AsyncSession 替身：execute / get / add / flush / delete。"""

    def __init__(self, rows=None, scalar=None, obj=None) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self._obj = obj
        self.added: List[Any] = []
        self.deleted: List[Any] = []
        self.flushed = 0

    async def execute(self, stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._scalar
        scalars = MagicMock()
        scalars.all.return_value = self._rows
        result.scalars.return_value = scalars
        return result

    async def get(self, model, pk):
        return self._obj

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        self.flushed += 1


class TestFhirPatientCRUD:
    @pytest.mark.asyncio
    async def test_create_patient(self):
        from app.api.v1.fhir import FHIRPatientCreate, create_patient

        db = _FakeDb(scalar=None)
        with patch("app.api.v1.fhir.AuditService") as audit_cls:
            audit_cls.return_value.log_action = AsyncMock()
            resp = await create_patient(
                request=FHIRPatientCreate(
                    patient_id="P900", name="张三", birth_date=date(1980, 1, 1), gender="male"
                ),
                db=db,
                current_user={"user_id": "doc_1"},
            )
            audit_cls.return_value.log_action.assert_awaited()

        assert resp["resourceType"] == "Patient"
        assert resp["id"] == "P900"
        assert db.added and db.added[0].patient_id == "P900"

    @pytest.mark.asyncio
    async def test_create_patient_conflict(self):
        from app.api.v1.errors import BadRequestException
        from app.api.v1.fhir import FHIRPatientCreate, create_patient

        existing = MagicMock()
        db = _FakeDb(scalar=existing)
        with pytest.raises(BadRequestException):
            await create_patient(
                request=FHIRPatientCreate(patient_id="P900"),
                db=db,
                current_user=None,
            )

    @pytest.mark.asyncio
    async def test_update_patient(self):
        from app.api.v1.fhir import FHIRPatientUpdate, update_patient

        patient = MagicMock()
        patient.patient_id = "P900"
        patient.name = "旧名"
        db = _FakeDb(scalar=patient)
        with patch("app.api.v1.fhir.ConsentRepository") as consent_cls:
            consent_cls.return_value.get_latest_consent = AsyncMock(
                return_value=MagicMock(status=ConsentStatus.GRANTED.value)
            )
            resp = await update_patient(
                id="P900",
                request=FHIRPatientUpdate(name="新名", gender="female"),
                db=db,
                current_user=None,
            )

        assert patient.name == "新名"
        assert patient.gender == "female"
        assert resp["id"] == "P900"

    @pytest.mark.asyncio
    async def test_delete_patient_writes_audit(self):
        from app.api.v1.fhir import delete_patient

        patient = MagicMock()
        patient.patient_id = "P900"
        db = _FakeDb(scalar=patient)
        with patch("app.api.v1.fhir.ConsentRepository") as consent_cls, patch(
            "app.api.v1.fhir.AuditService"
        ) as audit_cls:
            consent_cls.return_value.get_latest_consent = AsyncMock(
                return_value=MagicMock(status=ConsentStatus.GRANTED.value)
            )
            audit_cls.return_value.log_action = AsyncMock()
            resp = await delete_patient(
                id="P900", db=db, current_user={"user_id": "doc_1"}
            )
            audit_cls.return_value.log_action.assert_awaited()

        assert db.deleted == [patient]
        assert resp["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_update_patient_requires_consent(self):
        from app.api.v1.errors import PatientNotConsentedException
        from app.api.v1.fhir import FHIRPatientUpdate, update_patient

        db = _FakeDb(scalar=MagicMock())
        with patch("app.api.v1.fhir.ConsentRepository") as consent_cls:
            consent_cls.return_value.get_latest_consent = AsyncMock(return_value=None)
            with pytest.raises(PatientNotConsentedException):
                await update_patient(
                    id="P900",
                    request=FHIRPatientUpdate(name="x"),
                    db=db,
                    current_user=None,
                )

    @pytest.mark.asyncio
    async def test_get_imaging_study(self):
        from app.api.v1.fhir import get_imaging_study

        session = MagicMock(session_id="sess_1", patient_id="P001")
        db = _FakeDb(obj=session)
        resp = await get_imaging_study(id="sess_1", db=db, current_user=None)
        assert resp["resourceType"] == "ImagingStudy"
        assert resp["subject"]["reference"] == "Patient/P001"

    @pytest.mark.asyncio
    async def test_get_observation(self):
        from app.api.v1.fhir import get_observation

        diagnosis = MagicMock(request_id="req_1", session_id="sess_1", status="completed")
        db = _FakeDb(scalar=diagnosis, obj=MagicMock(patient_id="P001"))
        resp = await get_observation(id="obs-req_1", db=db, current_user=None)
        assert resp["resourceType"] == "Observation"
        assert resp["id"] == "obs-req_1"

    @pytest.mark.asyncio
    async def test_search_diagnostic_reports(self):
        from app.api.v1.fhir import search_diagnostic_reports

        session = MagicMock(session_id="sess_1", patient_id="P001")
        diagnosis = MagicMock(request_id="req_1", session_id="sess_1", status="completed")
        db = _FakeDb(rows=[session])
        # 第二次 execute 返回诊断结果列表
        calls = {"n": 0}
        original = db.execute

        async def _execute(stmt):
            calls["n"] += 1
            if calls["n"] == 1:
                result = MagicMock()
                scalars = MagicMock()
                scalars.all.return_value = [session]
                result.scalars.return_value = scalars
                return result
            result = MagicMock()
            scalars = MagicMock()
            scalars.all.return_value = [diagnosis]
            result.scalars.return_value = scalars
            return result

        db.execute = _execute
        bundle = await search_diagnostic_reports(
            subject="Patient/P001", db=db, current_user=None
        )
        assert bundle["total"] == 1
        assert bundle["entry"][0]["resource"]["id"] == "req_1"

    @pytest.mark.asyncio
    async def test_search_requires_subject(self):
        from app.api.v1.fhir import search_imaging_studies

        bundle = await search_imaging_studies(subject=None, db=_FakeDb(), current_user=None)
        assert bundle["total"] == 0
