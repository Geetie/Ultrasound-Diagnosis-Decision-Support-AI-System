"""A.88 守卫：签发门禁**不可绕过**（草稿接口 ``sign=True`` 曾直调 sign_report）。

## 真实缺陷（修复前）

``POST /api/v1/report``（``create_report_draft``）在 ``payload.sign`` 为 True 时
**直接**调 ``report_service.sign_report``，完全没有走 ``POST /{report_id}/sign``
端点里的 E1030 门禁（``check_completeness`` + ``missing_sign_blockers``）。
后果：带 ``sign=true`` 一个参数就能签发一份既缺「超声所见」也缺「诊断意见」的
报告 —— 门禁只是"另一个入口有"，等于没有。

## 修法与这里钉的口径

* 门禁判定抽成 **一个共用函数** ``report_service.sign_blockers_of(report)``；
* 错误契约组装抽成 **一个端点级函数** ``app.api.v1.report._assert_signable``；
* 两个签发入口（草稿 ``sign=True`` / ``/sign`` 端点）都只调这两个，口径不可能再漂移；
* 门禁拒绝时**不写** ``report_sign`` 审计（拒绝不是签发）；
* 审计 detail 保留 ``source`` 区分（``draft_request`` / ``sign_endpoint``）。

## 反证腿（本文件的重点）

``test_draft_path_bypass_would_be_red`` 直接演示"改回直调 ``sign_report`` 即红"：
同一份缺段报告，走门禁被 ``BadRequestException(E1030)`` 拒；不走门禁则**签成功**。
两条断言同时成立，说明这道用例真的能抓住回归，而不是"永远打印通过"。
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from app.api.v1.errors import BadRequestException
from app.api.v1.report import _assert_signable, create_report_draft, sign_report
from app.models.database import DiagnosisReport, DiagnosisResult
from app.models.schemas import ConsentStatus, ReportDraftRequest, ReportSignRequest
from app.services import report_service


class _FakeHSM:
    """最小 HSM 替身：签名对整段载荷敏感（正文变了签名就变）。"""

    def __init__(self) -> None:
        self.keys: dict[str, object] = {}

    def get_key_info(self, key_name: str):
        return self.keys.get(key_name)

    def generate_key_pair(self, key_name, key_size=2048, slot=None):
        self.keys[key_name] = {"slot": getattr(slot, "value", slot)}
        return {"key_name": key_name}

    def sign(self, key_name: str, data: bytes) -> bytes:
        return b"sig:" + hashlib.sha256(data).digest()

    def verify_signature(self, key_name: str, data: bytes, signature: bytes) -> bool:
        return signature == b"sig:" + hashlib.sha256(data).digest()


class _Audit:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def log_action(self, **kwargs):
        self.calls.append(kwargs)

    def actions(self) -> list[str]:
        return [c["action"] for c in self.calls]


class _FakeReportRepo:
    """内存报告仓储（草稿 / 签发两条链路需要的最小接口）。"""

    def __init__(self) -> None:
        self.reports: dict[str, DiagnosisReport] = {}
        self._seq = 0
        self.db = MagicMock()

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
            report_id=kwargs.get("report_id") or f"rep_a88_{self._seq}",
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


class _ConsentRepo:
    async def get_latest_consent(self, patient_id):
        return MagicMock(status=ConsentStatus.GRANTED.value)


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


def _session() -> MagicMock:
    session = MagicMock()
    session.session_id = "sess_a88"
    session.patient_id = "P001"
    session.knowledge_base_version = "kb_v1.2.0"
    return session


def _result_incomplete() -> DiagnosisResult:
    """**缺段**的判读结果：没有临床描述（→ findings 占位）、没有结论（→ diagnosis 占位）。

    这正是"带 sign=true 就能签出一份无证据无结论的报告"的输入形态。
    """
    return DiagnosisResult(
        request_id="req_a88_incomplete",
        session_id="sess_a88",
        organ="thyroid",
        tier="standard",
        clinical_text="",
        confidence=0.5,
        findings={"organ": "thyroid", "fusion_result": {"needs_review": False}},
        recommendations="",
        knowledge_ids=[],
        status="completed",
    )


def _result_complete() -> DiagnosisResult:
    return DiagnosisResult(
        request_id="req_a88_complete",
        session_id="sess_a88",
        organ="thyroid",
        tier="standard",
        clinical_text="甲状腺右叶低回声结节，边界不清",
        confidence=0.88,
        findings={"organ": "thyroid", "fusion_result": {"needs_review": False}},
        recommendations="TI-RADS 4a，建议细针穿刺活检。",
        knowledge_ids=["kb_1"],
        status="completed",
    )


class TestSharedGateFunction:
    """"一个共用函数"本身的口径（两个入口都依赖它，所以它必须先对）。"""

    def test_blockers_returned_for_placeholder_sections(self):
        report = DiagnosisReport(
            report_id="rep_x",
            request_id="req_x",
            session_id="sess_a88",
            organ="thyroid",
            status="draft",
            content={
                "organ": "thyroid",
                "request_id": "req_x",
                "findings": "（未提供临床描述）",
                "diagnosis": "（模型未产出结论，请人工评估）",
            },
        )

        assert set(report_service.sign_blockers_of(report)) == {"findings", "diagnosis"}

    def test_blockers_empty_when_evidence_and_conclusion_present(self):
        report = DiagnosisReport(
            report_id="rep_y",
            request_id="req_y",
            session_id="sess_a88",
            organ="thyroid",
            status="draft",
            content={
                "organ": "thyroid",
                "request_id": "req_y",
                "findings": "甲状腺右叶低回声结节",
                "diagnosis": "TI-RADS 4a",
            },
        )

        assert report_service.sign_blockers_of(report) == []

    def test_non_blocking_section_missing_is_not_a_blocker(self):
        """测量摘要/处置建议缺失只走前端知情确认 —— 不是签发阻塞项（A.32 口径）。"""
        report = DiagnosisReport(
            report_id="rep_z",
            request_id="req_z",
            session_id="sess_a88",
            organ="thyroid",
            status="draft",
            content={
                "organ": "thyroid",
                "request_id": "req_z",
                "findings": "甲状腺右叶低回声结节",
                "diagnosis": "TI-RADS 4a",
                "measurement_summary": "（未提供测量数据）",
                "clinical_actions": "（本次未生成处置建议）",
            },
        )

        assert report_service.sign_blockers_of(report) == []

    def test_tolerates_missing_content(self):
        """content 为空的报告（数据异常）按"全缺"处理，不抛 AttributeError。"""
        report = DiagnosisReport(
            report_id="rep_w",
            request_id="req_w",
            session_id="sess_a88",
            organ="thyroid",
            status="draft",
            content=None,
        )

        assert report_service.sign_blockers_of(report)

    def test_error_contract_is_E1030_with_chinese_labels(self):
        """错误契约：``E1030`` + 中文段落名 + ``detail.missing_blockers``。

        ⚠️ 断言打在**构造参数**上而不是 ``exc.detail``：``AppBaseException.__init__``
        （``app/api/v1/errors.py:115``）把 ``super().__init__(detail=error_message)``
        传给了 ``HTTPException``，后者会把 ``self.detail`` **覆盖**成错误文案 ——
        于是这个 ``detail=json(...)`` 目前到不了 HTTP 响应体（既有缺陷，影响**所有**
        ``AppBaseException``，不是本次改动引入；见交付报告的残余风险）。
        本次改动只保持调用侧契约不变，不顺手改异常基类（会把全部接口的错误体一起改掉）。
        """
        from app.api.v1 import report as report_api

        captured: dict = {}

        class _Recording(BadRequestException):
            def __init__(self, **kwargs):
                captured.update(kwargs)
                super().__init__(**kwargs)

        report = DiagnosisReport(
            report_id="rep_v",
            request_id="req_v",
            session_id="sess_a88",
            organ="thyroid",
            status="draft",
            content={"organ": "thyroid", "request_id": "req_v"},
        )

        with (
            patch.object(report_api, "BadRequestException", _Recording),
            pytest.raises(BadRequestException) as exc,
        ):
            _assert_signable(report)

        assert exc.value.error_code == "E1030"
        assert "超声所见" in exc.value.error_message
        assert "诊断意见" in exc.value.error_message
        # detail 是 JSON 字符串（既有契约形态），不是 dict
        assert isinstance(captured["detail"], str)
        assert set(json.loads(captured["detail"])["missing_blockers"]) == {
            "findings",
            "diagnosis",
        }


class TestDraftSignCannotBypassGate:
    """B1 的主体：草稿接口 ``sign=True`` 必须与 ``/sign`` 端点同一门禁。"""

    @pytest.mark.asyncio
    async def test_draft_with_sign_is_rejected_when_sections_missing(self):
        repo = _FakeReportRepo()
        audit = _Audit()

        with (
            patch("app.security.hsm.hsm_manager", _FakeHSM()),
            pytest.raises(BadRequestException) as exc,
        ):
            await create_report_draft(
                payload=ReportDraftRequest(request_id="req_a88_incomplete", sign=True),
                report_repo=repo,
                diagnosis_repo=_DiagnosisRepo(_result_incomplete()),
                session_repo=_SessionRepo(_session()),
                consent_repo=_ConsentRepo(),
                audit_service=audit,
                current_user={"user_id": "doc_1"},
            )

        assert exc.value.error_code == "E1030"
        assert "超声所见" in exc.value.error_message
        # 被拒 = 没签发：库里那份仍是草稿，且**没有** report_sign 审计
        (report,) = repo.reports.values()
        assert report.status == "draft"
        assert report.signature is None
        assert "report_sign" not in audit.actions()

    @pytest.mark.asyncio
    async def test_draft_with_sign_and_push_does_not_push_unsigned(self):
        """门禁拒绝后不许继续走到推送（``push=True`` 不能变成"推一份未签报告"）。"""
        repo = _FakeReportRepo()

        with (
            patch("app.security.hsm.hsm_manager", _FakeHSM()),
            pytest.raises(BadRequestException),
        ):
            await create_report_draft(
                payload=ReportDraftRequest(
                    request_id="req_a88_incomplete", sign=True, push=True
                ),
                report_repo=repo,
                diagnosis_repo=_DiagnosisRepo(_result_incomplete()),
                session_repo=_SessionRepo(_session()),
                consent_repo=_ConsentRepo(),
                audit_service=_Audit(),
                current_user=None,
            )

        (report,) = repo.reports.values()
        assert report.pacs_status in (None, "pending")
        assert report.status == "draft"

    @pytest.mark.asyncio
    async def test_draft_with_sign_succeeds_when_sections_present(self):
        """门禁不是死路：内容齐全时草稿一步签发照常成功，审计带 source=draft_request。"""
        repo = _FakeReportRepo()
        audit = _Audit()

        with patch("app.security.hsm.hsm_manager", _FakeHSM()):
            resp = await create_report_draft(
                payload=ReportDraftRequest(request_id="req_a88_complete", sign=True),
                report_repo=repo,
                diagnosis_repo=_DiagnosisRepo(_result_complete()),
                session_repo=_SessionRepo(_session()),
                consent_repo=_ConsentRepo(),
                audit_service=audit,
                current_user={"user_id": "doc_1"},
            )

        assert resp.signature_present is True
        assert audit.actions() == ["report_draft", "report_sign"]
        sign_call = next(c for c in audit.calls if c["action"] == "report_sign")
        assert sign_call["detail"]["source"] == "draft_request"

    @pytest.mark.asyncio
    async def test_sign_endpoint_keeps_source_label(self):
        """``/sign`` 端点的审计 source 仍是 sign_endpoint（两处可区分）。"""
        repo = _FakeReportRepo()
        audit = _Audit()

        with patch("app.security.hsm.hsm_manager", _FakeHSM()):
            draft = await create_report_draft(
                payload=ReportDraftRequest(request_id="req_a88_complete"),
                report_repo=repo,
                diagnosis_repo=_DiagnosisRepo(_result_complete()),
                session_repo=_SessionRepo(_session()),
                consent_repo=_ConsentRepo(),
                audit_service=_Audit(),
                current_user={"user_id": "doc_1"},
            )
            audit.calls.clear()
            await sign_report(
                report_id=draft.report_id,
                payload=ReportSignRequest(),
                report_repo=repo,
                audit_service=audit,
                current_user={"user_id": "doc_1"},
            )

        assert audit.actions() == ["report_sign"]
        assert audit.calls[0]["detail"]["source"] == "sign_endpoint"

    @pytest.mark.asyncio
    async def test_sign_endpoint_and_draft_path_agree_on_same_report(self):
        """**同一份缺段报告，两条入口给出同一个错误契约**（口径不可能再漂移）。"""
        repo = _FakeReportRepo()
        with patch("app.security.hsm.hsm_manager", _FakeHSM()):
            draft = await create_report_draft(
                payload=ReportDraftRequest(request_id="req_a88_incomplete"),
                report_repo=repo,
                diagnosis_repo=_DiagnosisRepo(_result_incomplete()),
                session_repo=_SessionRepo(_session()),
                consent_repo=_ConsentRepo(),
                audit_service=_Audit(),
                current_user=None,
            )

            with pytest.raises(BadRequestException) as endpoint_exc:
                await sign_report(
                    report_id=draft.report_id,
                    payload=ReportSignRequest(),
                    report_repo=repo,
                    audit_service=_Audit(),
                    current_user=None,
                )

            with pytest.raises(BadRequestException) as draft_exc:
                await create_report_draft(
                    payload=ReportDraftRequest(request_id="req_a88_incomplete", sign=True),
                    report_repo=repo,
                    diagnosis_repo=_DiagnosisRepo(_result_incomplete()),
                    session_repo=_SessionRepo(_session()),
                    consent_repo=_ConsentRepo(),
                    audit_service=_Audit(),
                    current_user=None,
                )

        assert endpoint_exc.value.error_code == draft_exc.value.error_code == "E1030"
        assert endpoint_exc.value.error_message == draft_exc.value.error_message


class TestBypassWouldBeRed:
    """**反证腿**：证明这道门禁用例真的能响，而不是永远通过。

    把草稿路径改回"直调 sign_report（跳过门禁）"—— 也就是修复前的形状 —— 同一份
    缺段报告会**签发成功**。因此上面 ``test_draft_with_sign_is_rejected_...``
    一旦有人回退接线，必然转红。
    """

    @pytest.mark.asyncio
    async def test_direct_sign_report_call_skips_gate(self):
        repo = _FakeReportRepo()
        draft = await report_service.generate_report_draft(
            result=_result_incomplete(), report_repo=repo, session=_session()
        )

        with patch("app.security.hsm.hsm_manager", _FakeHSM()):
            signed = await report_service.sign_report(
                report=draft, report_repo=repo, actor_id="doc_1"
            )

        # 直调 = 绕过 = 缺段报告被签发了（这正是被修掉的缺陷形态）
        assert signed.signature is not None
        assert signed.status == "signed"
        # 而门禁口径明确认为它**不该**被签发
        assert set(report_service.sign_blockers_of(draft)) == {"findings", "diagnosis"}

    @pytest.mark.asyncio
    async def test_bypass_regression_is_detectable_by_audit_absence(self):
        """回退后连"没写签发审计"这条也会失守，两条断言互相独立地钉住回归。"""
        repo = _FakeReportRepo()
        audit = _Audit()

        # 修复后的接线：门禁拒绝 → 无 report_sign 审计
        with (
            patch("app.security.hsm.hsm_manager", _FakeHSM()),
            pytest.raises(BadRequestException),
        ):
            await create_report_draft(
                payload=ReportDraftRequest(request_id="req_a88_incomplete", sign=True),
                report_repo=repo,
                diagnosis_repo=_DiagnosisRepo(_result_incomplete()),
                session_repo=_SessionRepo(_session()),
                consent_repo=_ConsentRepo(),
                audit_service=audit,
                current_user=None,
            )
        assert "report_sign" not in audit.actions()

        # 若有人删掉 _assert_signable 那一行，这条就会变成 ["report_draft","report_sign"]
        assert audit.actions() == ["report_draft"]


class TestGateWiringIsSourceLevel:
    """源码级守卫：两个入口都必须经过**同一个**函数（防止内联复制粘贴回来）。"""

    def test_draft_path_calls_shared_gate(self):
        import inspect

        from app.api.v1 import report as report_api

        source = inspect.getsource(report_api.create_report_draft)
        assert "_assert_signable(report)" in source
        # 草稿路径不许自己再内联一份门禁判定
        assert "missing_sign_blockers" not in source

    def test_sign_endpoint_calls_shared_gate(self):
        import inspect

        from app.api.v1 import report as report_api

        source = inspect.getsource(report_api.sign_report)
        assert "_assert_signable(report)" in source
        assert "missing_sign_blockers" not in source

    def test_service_layer_does_not_import_api_errors(self):
        """判定留在服务层、契约组装在 API 层（import-linter 契约 2 的对应约定）。"""
        import inspect

        source = inspect.getsource(report_service.sign_blockers_of)
        assert "BadRequestException" not in source
        assert "app.api" not in source
