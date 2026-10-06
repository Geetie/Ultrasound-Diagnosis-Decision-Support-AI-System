"""报告签发门禁 + 医生补录（A.32，2026-10-06）。

口径（写在 report_service.SIGN_BLOCKING_SECTIONS 的注释里，这里钉行为）：
  * findings（超声所见）/ diagnosis（诊断意见）缺失 -> **拒绝签发**（E1030）。
    这两段是报告的证据与结论，缺了就是无证据断言 / 空结论。
  * 其余段落缺失 -> 前端知情确认，服务端不禁。
  * 补录（PATCH）只允许草稿、只接受白名单键、必须重渲染并重算摘要；
    出处记录进 content["doctor_supplemented"]（段落出处是临床文书的一部分）。

门禁与补录是一对：只上补录，缺段落的报告照样签；只上门禁，医生被堵死。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.errors import BadRequestException
from app.api.v1.report import sign_report, update_report_sections
from app.models.database import DiagnosisReport
from app.models.schemas import ReportSignRequest, ReportUpdateRequest
from app.services import report_service


class _Repo:
    """内存仓储：门禁 / 补录两条链路需要的最小接口。"""

    def __init__(self, report) -> None:
        self.report = report
        self.db = MagicMock()
        self.db.flush = AsyncMock()

    async def get_report(self, report_id: str):
        return self.report if report_id == getattr(self.report, "report_id", None) else None

    async def update_sections(self, report_id, *, content, rendered, content_digest):
        if self.report is None or report_id != self.report.report_id:
            return None
        self.report.content = content
        self.report.rendered = rendered
        self.report.content_digest = content_digest
        return self.report

    async def mark_signed(self, report_id, *, signature, signing_key_name, signed_by=None):
        from datetime import datetime, timezone

        if self.report is None or report_id != self.report.report_id:
            return None
        self.report.signature = signature
        self.report.signing_key_name = signing_key_name
        self.report.signed_by = signed_by
        self.report.signed_at = datetime.now(timezone.utc)
        self.report.status = "signed"
        return self.report


def _report(**overrides) -> DiagnosisReport:
    row = DiagnosisReport(
        report_id="rep_gate_1",
        request_id="req_1",
        session_id="sess_1",
        patient_id="P001",
        organ="thyroid",
        status="draft",
        content={
            "organ": "thyroid",
            "request_id": "req_1",
            "findings": "（未提供临床描述）",
            "diagnosis": "（模型未产出结论，请人工评估）",
            "measurement_summary": "（未提供测量数据）",
            "clinical_actions": "（本次未生成处置建议）",
        },
        rendered="超声诊断报告（草稿）",
        content_digest="digest-0",
        template_version="report-template-v1",
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class TestMissingSignBlockers:
    def test_findings_and_diagnosis_missing_are_blockers(self):
        completeness = report_service.check_completeness(_report().content, organ="thyroid")
        blockers = report_service.missing_sign_blockers(completeness)
        assert set(blockers) == {"findings", "diagnosis"}

    def test_only_non_blocking_sections_missing_is_not_a_blocker(self):
        content = {
            "organ": "thyroid",
            "request_id": "req_1",
            "generated_at": "2026-10-06T00:00:00+00:00",
            "template_version": "report-template-v1",
            "findings": "甲状腺右叶低回声结节",
            "diagnosis": "TI-RADS 4a",
            "measurement_summary": "（未提供测量数据）",
            "clinical_actions": "（本次未生成处置建议）",
        }
        completeness = report_service.check_completeness(content, organ="thyroid")
        assert completeness["missing"] == ["measurement_summary", "clinical_actions"]
        assert report_service.missing_sign_blockers(completeness) == []

    def test_labels_are_human_readable(self):
        """门禁消息必须说人话（医生看得懂缺什么），不是字段名。"""
        assert [report_service.section_label(f) for f in ("findings", "diagnosis")] == [
            "超声所见",
            "诊断意见",
        ]

class TestUpdateReportSections:
    @pytest.mark.asyncio
    async def test_supplement_merges_rerenders_and_recomputes(self):
        report = _report()
        repo = _Repo(report)

        updated = await report_service.update_report_sections(
            report=report,
            sections={
                "findings": "甲状腺右叶中极低回声结节，边界不清，内见微钙化",
                "measurement_summary": "结节长径 12.4mm，短径 8.1mm",
            },
            report_repo=repo,
        )

        assert "甲状腺右叶中极低回声结节" in updated.rendered
        assert "12.4mm" in updated.rendered
        # 摘要重算（与正文一致，否则验签会误判"正文被改动"）
        assert updated.content_digest != "digest-0"
        # 出处：这两段是医生补的，不是 AI 产出
        assert set(updated.content["doctor_supplemented"]) == {
            "findings",
            "measurement_summary",
        }
        completeness = report_service.check_completeness(
            updated.content, organ=updated.organ
        )
        assert "findings" not in completeness["missing"]
        assert "measurement_summary" not in completeness["missing"]
        assert set(report_service.missing_sign_blockers(completeness)) == {"diagnosis"}

    @pytest.mark.asyncio
    async def test_rejects_signed_report(self):
        report = _report(status="signed", signature="c2ln")
        repo = _Repo(report)

        with pytest.raises(ValueError, match="only draft"):
            await report_service.update_report_sections(
                report=report,
                sections={"findings": "补录"},
                report_repo=repo,
            )
        # 拒绝的同时不许有任何写入
        assert report.rendered == "超声诊断报告（草稿）"

    @pytest.mark.asyncio
    async def test_ignores_non_whitelisted_keys(self):
        report = _report()
        repo = _Repo(report)

        await report_service.update_report_sections(
            report=report,
            sections={"findings": "补录所见", "patient_id": "P-ATTACKER"},
            report_repo=repo,
        )

        assert report.content["findings"] == "补录所见"
        assert "patient_id" not in report.content
        assert "evidence" not in report.content

    @pytest.mark.asyncio
    async def test_rejects_empty_sections(self):
        report = _report()
        repo = _Repo(report)

        with pytest.raises(ValueError, match="no supplementable"):
            await report_service.update_report_sections(
                report=report, sections={"findings": "   "}, report_repo=repo
            )



class TestReportSignGateAPI:
    """PATCH 补录 + 签发门禁（直接调用端点函数 —— 本仓库 report 测试的既有惯例，
    见 test_report_api.py：TestClient 的鉴权覆盖在 report 路由上不生效，
    而 report 端点的逻辑本身不依赖 HTTP 层，直调反而稳定）。"""

    @staticmethod
    def _hsm():
        hsm = MagicMock()
        hsm.get_key_info.return_value = {"key_name": "report_signing_v1"}
        hsm.generate_key_pair.return_value = {"key_name": "report_signing_v1"}
        hsm.sign.side_effect = lambda key, data: b"sig:" + bytes(data)[:8]
        hsm.verify_signature.return_value = True
        return hsm

    @pytest.mark.asyncio
    async def test_patch_supplements_draft(self):
        report = _report()
        repo = _Repo(report)
        audit = AsyncMock()

        resp = await update_report_sections(
            report_id=report.report_id,
            payload=ReportUpdateRequest(
                sections={"findings": "甲状腺右叶低回声结节，边界不清"}
            ),
            report_repo=repo,
            audit_service=audit,
            current_user={"user_id": "doc_1"},
        )

        assert "甲状腺右叶低回声结节" in resp.rendered
        assert "findings" not in resp.completeness["missing"]
        audit.log_action.assert_awaited_once()
        assert audit.log_action.await_args.kwargs["action"] == "report_supplement"

    @pytest.mark.asyncio
    async def test_patch_rejects_signed(self):
        report = _report(status="signed", signature="c2ln")
        with pytest.raises(BadRequestException):
            await update_report_sections(
                report_id=report.report_id,
                payload=ReportUpdateRequest(sections={"findings": "补录"}),
                report_repo=_Repo(report),
                audit_service=AsyncMock(),
                current_user=None,
            )

    @pytest.mark.asyncio
    async def test_sign_blocked_when_findings_missing(self):
        report = _report()
        repo = _Repo(report)

        with pytest.raises(BadRequestException) as exc:
            await sign_report(
                report_id=report.report_id,
                payload=ReportSignRequest(),
                report_repo=repo,
                audit_service=AsyncMock(),
                current_user={"user_id": "doc_1"},
            )

        assert exc.value.error_code == "E1030"
        assert "超声所见" in exc.value.error_message
        assert "诊断意见" in exc.value.error_message
        # 被拒的报告不许被签名
        assert repo.report.signature is None

    @pytest.mark.asyncio
    async def test_sign_succeeds_after_supplement(self):
        report = _report()
        repo = _Repo(report)

        await update_report_sections(
            report_id=report.report_id,
            payload=ReportUpdateRequest(
                sections={
                    "findings": "甲状腺右叶低回声结节，边界不清",
                    "diagnosis": "TI-RADS 4a，考虑恶性可能",
                }
            ),
            report_repo=repo,
            audit_service=AsyncMock(),
            current_user={"user_id": "doc_1"},
        )

        with patch("app.security.hsm.hsm_manager", self._hsm()):
            resp = await sign_report(
                report_id=report.report_id,
                payload=ReportSignRequest(),
                report_repo=repo,
                audit_service=AsyncMock(),
                current_user={"user_id": "doc_1"},
            )

        assert resp.signature_present is True
        # 签名针对的是补录后的正文
        assert "甲状腺右叶低回声结节" in resp.rendered
