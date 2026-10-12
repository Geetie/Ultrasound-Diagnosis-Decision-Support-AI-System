"""A.52 知识库版本口径守卫（会话 / 报告 / 判读结果 / 管线 findings）。

背景（2026-10-12 A.51 批次端到端摸排实测）：
    生产 KB 已切到 `kb_v1.3.0`（qdrant 1257 点 + PG knowledge_chunks 同版本），
    但**会话记录 / 报告「知识库版本」/ 判读结果 `knowledge_base_version`** 全部
    仍是 `kb_v1.0.0` —— Phase 0A 的占位常量 `settings.phase0a_mock_kb_version`。
    真机铁证：同一份报告的证据行是 `kb_v1.3.0:pdf_cardiac_...`，而版本标注是
    `知识库版本：kb_v1.0.0`（自相矛盾，溯源不可信）。

本模块钉住四条不变量：
  1. 仓储缺省值 == `settings.rag_kb_version`（不再写死占位常量）；
  2. 调用方显式传值时以其为准（缺省不覆盖）；
  3. `app/api/v1/diagnosis.py` 不再引用 `phase0a_mock_kb_version`；
  4. 管线 findings 写入 `knowledge_base_version`，报告层从 session 取（不写死）。
"""
from __future__ import annotations

import inspect
import pathlib
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.track_api]

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture
def session_repo():
    from app.repositories.session_repository import SessionRepository

    return SessionRepository(AsyncMock(spec=AsyncSession))


def _settings():
    from app.config.settings import settings

    return settings


class TestSessionRepositoryKbVersion:
    """仓储层缺省值 = 生产真值。"""

    async def test_default_uses_settings_rag_kb_version(self, session_repo):
        from app.models.schemas import ConsentStatus

        session = await session_repo.create_session(
            patient_id="patient_kbv_001",
            device_id="device_kbv",
            target_organs=["thyroid"],
            consent_status=ConsentStatus.GRANTED,
        )
        assert session.knowledge_base_version == _settings().rag_kb_version
        assert session.knowledge_base_version != "kb_v1.0.0" or _settings().rag_kb_version == "kb_v1.0.0"

    async def test_explicit_value_wins(self, session_repo):
        from app.models.schemas import ConsentStatus

        session = await session_repo.create_session(
            patient_id="patient_kbv_002",
            device_id="device_kbv",
            target_organs=["cardiac"],
            consent_status=ConsentStatus.GRANTED,
            knowledge_base_version="kb_explicit_9.9.9",
        )
        assert session.knowledge_base_version == "kb_explicit_9.9.9"


class TestNoMockKbVersionInDiagnosisApi:
    """判读 API 不得再引用 Phase 0A 占位常量。"""

    def test_diagnosis_api_has_no_mock_kb_version(self):
        """断言「引用」而不是「提及」：历史注释里保留常量名是允许的。"""
        src = (REPO_ROOT / "app" / "api" / "v1" / "diagnosis.py").read_text(encoding="utf-8")
        assert "settings.phase0a_mock_kb_version" not in src, (
            "diagnosis.py 又引用了 settings.phase0a_mock_kb_version："
            "知识库版本会退回 Phase 0A 占位值"
        )

    def test_diagnosis_api_uses_settings_rag_kb_version(self):
        from app.api.v1 import diagnosis

        src = inspect.getsource(diagnosis)
        assert "settings.rag_kb_version" in src


class TestPipelineWritesKbVersion:
    """管线 findings 必须写回真实库版本（结果读路径据此取值）。"""

    def test_pipeline_findings_has_knowledge_base_version(self):
        src = (REPO_ROOT / "app" / "services" / "diagnosis_pipeline.py").read_text(encoding="utf-8")
        assert '"knowledge_base_version": settings.rag_kb_version' in src, (
            "管线 findings 缺 knowledge_base_version —— 结果接口会退回占位常量"
        )

    def test_session_api_passes_kb_version(self):
        from app.api.v1 import session

        src = inspect.getsource(session.create_session)
        assert "knowledge_base_version=settings.rag_kb_version" in src

    def test_report_renders_kb_version_from_session(self):
        """报告「知识库版本」必须来自会话记录，而不是写死常量。"""
        src = (REPO_ROOT / "app" / "services" / "report_service.py").read_text(encoding="utf-8")
        assert 'getattr(session, "knowledge_base_version"' in src
