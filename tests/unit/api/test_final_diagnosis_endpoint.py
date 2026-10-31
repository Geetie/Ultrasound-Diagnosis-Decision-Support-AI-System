"""05-3 S1：最终诊断回填端点守卫。

环境说明：本仓 httpx≥0.28 × starlette 0.36.3 下 TestClient 不可用（SEC-05
依赖债，既有 api 测试同挂），故采用与 test_b4_t2a_consumer.py 同款的
**源码级守卫 + 纯函数直测**；TestClient 恢复后应补齐请求级用例
（匿名 401 / 医生角色 403 / QC 200）。

守卫点：
1. 权限位 ``write_final_diagnosis`` 只登记给 QUALITY_CONTROL（审计独立性：
   写金标准的人不该是出初始结论的同角色）；
2. 端点拒绝非法 source（与 p76 审计脚本 SOURCES 同源）；
3. repo 方法只写 4 个审计列，不动判读产物（伪对象直测）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.models.schemas import FINAL_DIAGNOSIS_SOURCES, FinalDiagnosisRequest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _p76_module():
    spec = importlib.util.spec_from_file_location(
        "p76", _REPO_ROOT / "scripts" / "audit" / "p76_missed_rate_audit.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPermissionRegistration:
    def test_permission_registered_fail_closed(self):
        from app.security.auth import KNOWN_PERMISSIONS, check_permission

        assert "write_final_diagnosis" in KNOWN_PERMISSIONS
        # 未登记角色一律拒绝（fail-closed）
        assert check_permission("write_final_diagnosis", {"role": "ULTRASOUND_DOCTOR"}) is False
        assert check_permission("write_final_diagnosis", {"role": "DEPARTMENT_HEAD"}) is False
        assert check_permission("write_final_diagnosis", {"role": "QUALITY_CONTROL"}) is True

    def test_admin_wildcard_covers_it(self):
        from app.security.auth import check_permission

        assert check_permission("write_final_diagnosis", {"role": "SYSTEM_ADMIN"}) is True


class TestSourceValidation:
    def test_sources_match_p76_script(self):
        """API 层枚举与审计脚本 SOURCES 同源（两边不一致 = 分母口径分裂）。"""
        mod = _p76_module()
        assert set(FINAL_DIAGNOSIS_SOURCES) == mod.SOURCES

    def test_request_rejects_empty_diagnosis(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FinalDiagnosisRequest(final_diagnosis="", final_diagnosis_source="pathology")

    def test_request_rejects_overlong_diagnosis(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FinalDiagnosisRequest(final_diagnosis="x" * 501, final_diagnosis_source="pathology")

    def test_valid_request_accepted(self):
        req = FinalDiagnosisRequest(
            final_diagnosis="恶性", final_diagnosis_source="pathology"
        )
        assert req.final_diagnosis_source == "pathology"


class TestRepoMethodContract:
    """mark_final_diagnosis 只写 4 个审计列，不动判读产物（伪对象直测）。"""

    async def test_writes_only_audit_columns(self):
        from app.repositories import diagnosis_repository as repo_mod

        class _FakeRow:
            request_id = "r1"
            session_id = "s1"
            final_diagnosis = None
            final_diagnosis_source = None
            final_diagnosis_at = None
            final_diagnosis_by = None
            # 判读产物（不得被改动）
            status = "completed"
            confidence = 0.82
            findings = {"x": 1}
            doctor_confirmed_at = "keep"

        row = _FakeRow()

        class _FakeDB:
            async def flush(self):
                pass

        class _FakeRepo(repo_mod.DiagnosisRepository):
            def __init__(self):  # 不连库
                self.db = _FakeDB()

            async def get_result(self, request_id):
                return row if request_id == "r1" else None

        repo = _FakeRepo()
        out = await repo_mod.DiagnosisRepository.mark_final_diagnosis(
            repo, "r1", "恶性", "pathology", "qc1"
        )
        assert out is row
        assert row.final_diagnosis == "恶性"
        assert row.final_diagnosis_source == "pathology"
        assert row.final_diagnosis_by == "qc1"
        assert row.final_diagnosis_at is not None
        # 判读产物逐项未动
        assert row.status == "completed"
        assert row.confidence == 0.82
        assert row.findings == {"x": 1}
        assert row.doctor_confirmed_at == "keep"

    async def test_unknown_request_returns_none(self):
        from app.repositories import diagnosis_repository as repo_mod

        class _FakeRepo(repo_mod.DiagnosisRepository):
            def __init__(self):
                self.db = None

            async def get_result(self, request_id):
                return None

        repo = _FakeRepo()
        assert (
            await repo_mod.DiagnosisRepository.mark_final_diagnosis(
                repo, "missing", "恶性", "pathology", "qc1"
            )
            is None
        )


class TestEndpointWiring:
    """源码级守卫：路由/权限/来源校验/审计必须接线（TestClient 恢复前）。"""

    def test_route_and_permission_wired(self):
        src = (_REPO_ROOT / "app" / "api" / "v1" / "diagnosis.py").read_text(encoding="utf-8")
        assert '"/{request_id}/final-diagnosis"' in src
        assert 'PermissionChecker("write_final_diagnosis")' in src
        # 来源校验必须用共享枚举（不得内联另一份清单）
        assert "payload.final_diagnosis_source not in FINAL_DIAGNOSIS_SOURCES" in src
        # 审计必须留痕
        assert 'action="set_final_diagnosis"' in src

    def test_repo_does_not_touch_diagnosis_columns(self):
        src = (
            _REPO_ROOT / "app" / "repositories" / "diagnosis_repository.py"
        ).read_text(encoding="utf-8")
        body = src.split("async def mark_final_diagnosis", 1)[1].split("async def", 1)[0]
        for banned in ("status =", "confidence =", "findings =", "doctor_confirmed_at ="):
            assert banned not in body, f"mark_final_diagnosis 不得改判读产物: {banned}"
