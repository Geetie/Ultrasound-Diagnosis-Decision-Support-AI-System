"""第二梯队③ 历史对比阅片（A.54）：患者历史检查端点守卫。

钉住三件事：
1. **schema 字段集**（含相邻类不被"插入边界吃掉"—— A.51 踩过：新增 `PatientListResponse`
   时吃掉了相邻 `DeviceRemoteRecoveryResponse.expires_at`，静态检查与 import 都发现不了）；
2. **装配语义**（结论优先报告、无报告回退 findings；分割保留 available=False 的 reason；
   JSONB 器官脏数据容错；影像计数与代表帧）；
3. **端点行为**（同器官 ≥2 次 → `comparable_organs`；organ 过滤；无患者 vs 无检查；
   读历史必须留审计）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.schemas import (
    DeviceDeregisterResponse,
    PatientHistoryItem,
    PatientHistoryResponse,
    PatientHistorySegmentation,
)

pytestmark = [pytest.mark.track_api]


class TestSchemaFieldSets:
    def test_history_item_fields(self):
        assert set(PatientHistoryItem.model_fields) == {
            "session_id",
            "created_at",
            "status",
            "target_organs",
            "organ",
            "knowledge_base_version",
            "request_id",
            "confidence",
            "conclusion",
            "report_id",
            "report_status",
            "report_signed",
            "signed_at",
            "image_count",
            "first_image_id",
            "segmentation",
        }

    def test_history_response_fields(self):
        assert set(PatientHistoryResponse.model_fields) == {
            "patient_id",
            "patient_exists",
            "total",
            "organs",
            "comparable_organs",
            "items",
        }

    def test_segmentation_fields(self):
        assert set(PatientHistorySegmentation.model_fields) == {
            "available",
            "area_mm2",
            "area_px",
            "frame_index",
            "total_frames",
            "selection_policy",
            "organ",
            "reason",
        }

    def test_neighbour_class_untouched(self):
        """相邻类字段不得被插入动作吃掉（A.51 真实事故）。"""
        assert set(DeviceDeregisterResponse.model_fields) == {"device_id", "status"}


def _session(sid: str, **kw):
    base = {
        "session_id": sid,
        "patient_id": "p-1",
        "device_id": "dev",
        "status": "completed",
        "target_organs": ["thyroid"],
        "knowledge_base_version": "kb_v1.3.0",
        "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _result(**kw):
    base = {
        "request_id": "req_1",
        "session_id": "s1",
        "organ": "thyroid",
        "confidence": 0.72,
        "findings": {"segmentation": {"available": True, "area_mm2": 12.5}},
        "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _report(**kw):
    base = {
        "report_id": "rep_1",
        "session_id": "s1",
        "organ": "thyroid",
        "status": "signed",
        "content": {"diagnosis": "甲状腺右叶结节，考虑良性。"},
        "signed_at": datetime(2026, 10, 1, 1, tzinfo=timezone.utc),
        "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
    }
    base.update(kw)
    return SimpleNamespace(**base)


class TestBuildHistoryItems:
    def test_conclusion_prefers_report_over_findings(self):
        from app.api.v1.patients import build_history_items

        items = build_history_items(
            [_session("s1")],
            {"s1": _result(findings={"fusion_result": {"final_response": {"content": "管线原始结论"}}})},
            {"s1": _report()},
            {"s1": (2, "img_a")},
        )
        assert len(items) == 1
        assert items[0].conclusion == "甲状腺右叶结节，考虑良性。"
        assert items[0].image_count == 2
        assert items[0].first_image_id == "img_a"
        assert items[0].report_signed is True

    def test_conclusion_falls_back_to_findings_when_no_report(self):
        from app.api.v1.patients import build_history_items

        items = build_history_items(
            [_session("s1")],
            {"s1": _result(findings={"fusion_result": {"final_response": {"content": "管线原始结论"}}})},
            {},
            {},
        )
        assert items[0].conclusion == "管线原始结论"
        assert items[0].report_id is None
        assert items[0].report_signed is False

    def test_conclusion_truncated(self):
        from app.api.v1.patients import CONCLUSION_PREVIEW_CHARS, build_history_items

        long_text = "结" * (CONCLUSION_PREVIEW_CHARS + 200)
        items = build_history_items(
            [_session("s1")],
            {"s1": _result(findings={"fusion_result": {"final_response": {"content": long_text}}})},
            {},
            {},
        )
        assert len(items[0].conclusion) == CONCLUSION_PREVIEW_CHARS

    def test_segmentation_unavailable_keeps_reason(self):
        from app.api.v1.patients import build_history_items

        items = build_history_items(
            [_session("s1")],
            {
                "s1": _result(
                    findings={
                        "segmentation": {
                            "available": False,
                            "reason": "bypass_disabled",
                        }
                    }
                )
            },
            {},
            {},
        )
        seg = items[0].segmentation
        assert seg is not None and seg.available is False
        assert seg.reason == "bypass_disabled"

    def test_dirty_target_organs_tolerated(self):
        from app.api.v1.patients import build_history_items

        items = build_history_items(
            [_session("s1", target_organs=None), _session("s2", target_organs="thyroid")],
            {},
            {},
            {},
        )
        assert items[0].target_organs == []
        assert items[1].target_organs == ["thyroid"]

    def test_session_without_result_has_no_conclusion(self):
        from app.api.v1.patients import build_history_items

        items = build_history_items([_session("s1")], {}, {}, {})
        assert items[0].conclusion is None
        assert items[0].organ is None
        assert items[0].request_id is None


# ---------------------------------------------------------------------------
# 端点级：用 dependency_overrides + 假 DB 会话（execute 按调用顺序返回预置行）
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows, scalar=None):
        self._rows = rows
        self._scalar = scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))

    def all(self):
        return list(self._rows)

    def scalar_one(self):
        return self._scalar


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    async def execute(self, _stmt):
        self.calls += 1
        return self._results.pop(0)


@pytest.fixture
def history_env():
    from app.config.database import get_db_session
    from app.dependencies import get_audit_service
    from app.main import app
    from app.security.auth import get_current_active_user
    from app.services.audit_service import AuditService

    audit = AsyncMock(spec=AuditService)
    audit.log_action = AsyncMock()
    app.dependency_overrides[get_audit_service] = lambda: audit
    app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doc-1"}
    yield {"app": app, "audit": audit, "db_dep": get_db_session}
    app.dependency_overrides.clear()


def _wire(db, history_env):
    history_env["app"].dependency_overrides[history_env["db_dep"]] = lambda: db


def test_history_returns_items_and_comparable_organs(test_client, history_env):
    sessions = [
        _session("s2", created_at=datetime(2026, 10, 2, tzinfo=timezone.utc)),
        _session("s1", created_at=datetime(2026, 10, 1, tzinfo=timezone.utc)),
    ]
    db = _FakeSession(
        [
            _FakeResult(sessions),
            _FakeResult([], scalar=1),  # patient_exists
            _FakeResult(
                [
                    _result(session_id="s2", request_id="req_2", organ="thyroid"),
                    _result(session_id="s1", request_id="req_1", organ="thyroid"),
                ]
            ),
            _FakeResult([_report(session_id="s1", report_id="rep_1")]),
            _FakeResult([("s1", "img_a"), ("s1", "img_b"), ("s2", "img_c")]),
        ]
    )
    _wire(db, history_env)

    resp = test_client.get("/api/v1/patients/p-1/sessions")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["patient_exists"] is True
    assert data["total"] == 2
    assert data["organs"] == ["thyroid"]
    # 同器官出现 2 次 → 可做前后对比
    assert data["comparable_organs"] == ["thyroid"]
    by_sid = {it["session_id"]: it for it in data["items"]}
    assert by_sid["s1"]["image_count"] == 2 and by_sid["s1"]["first_image_id"] == "img_a"
    assert by_sid["s1"]["report_id"] == "rep_1"
    assert by_sid["s2"]["report_id"] is None
    # 读历史必须留审计
    history_env["audit"].log_action.assert_awaited()


def test_history_organ_filter_drops_other_organs(test_client, history_env):
    sessions = [_session("s1"), _session("s2")]
    db = _FakeSession(
        [
            _FakeResult(sessions),
            _FakeResult([], scalar=1),
            _FakeResult(
                [
                    _result(session_id="s1", organ="thyroid"),
                    _result(session_id="s2", organ="cardiac"),
                ]
            ),
            _FakeResult([]),
            _FakeResult([]),
        ]
    )
    _wire(db, history_env)

    resp = test_client.get("/api/v1/patients/p-1/sessions?organ=thyroid")
    assert resp.status_code == 200
    data = resp.json()
    assert [it["session_id"] for it in data["items"]] == ["s1"]
    assert data["total"] == 1


def test_history_empty_for_unknown_patient_marks_not_exists(test_client, history_env):
    db = _FakeSession([_FakeResult([]), _FakeResult([], scalar=0)])
    _wire(db, history_env)

    resp = test_client.get("/api/v1/patients/ghost/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "patient_id": "ghost",
        "patient_exists": False,
        "total": 0,
        "organs": [],
        "comparable_organs": [],
        "items": [],
    }


@pytest.mark.parametrize("bad_limit", [0, 101])
def test_history_limit_validation(test_client, history_env, bad_limit):
    db = _FakeSession([_FakeResult([]), _FakeResult([], scalar=1)])
    _wire(db, history_env)
    resp = test_client.get(f"/api/v1/patients/p-1/sessions?limit={bad_limit}")
    assert resp.status_code == 422
