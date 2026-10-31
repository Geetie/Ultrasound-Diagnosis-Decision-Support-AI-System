"""评测集进度端点守卫（`GET /api/v1/eval-set/stats`，A.61）。

守三件事：
1. **认证**：匿名绝不允许 200（聚合计数虽非 PHI，但也不对外）；
2. **缺报告不是错误**：`available=false` + 可读 reason，HTTP **200**（零静默口径：
   界面据此显示「尚未采集」，而不是把 404/500 当"不用管"）；
3. **报告损坏不许 500**：JSON 坏了 → `available=false` + 原因（war 日志 + 可读提示）。
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.eval_set import router as eval_set_router
from app.security.auth import get_current_active_user

_URL = "/api/v1/eval-set/stats"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(eval_set_router, prefix="/api/v1/eval-set")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def as_doctor(client):
    """注入医生身份（本端点口径 = 任意已登录用户可读）。"""

    def _fake_user():
        return {"role": "ULTRASOUND_DOCTOR", "user_id": "doc-1"}

    client.app.dependency_overrides[get_current_active_user] = _fake_user
    yield client
    client.app.dependency_overrides.pop(get_current_active_user, None)


def test_匿名请求被拒(client):
    resp = client.get(_URL)
    assert resp.status_code in (401, 403, 422), "匿名绝不能拿到进度数据"


def test_缺报告返回200且可读(as_doctor, monkeypatch, tmp_path):
    # 密封化（A.102）：部署机 ~/uai/reports/ 有真实报告文件，fallback 候选
    # 会命中 → available=True。把 HOME 钉进 tmp_path 使全部候选落空。
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("UAI_EVAL_SET_REPORT", str(tmp_path / "missing.json"))
    resp = as_doctor.get(_URL)
    assert resp.status_code == 200, "尚未采集是正常状态，不该 404/500"
    body = resp.json()
    assert body["available"] is False
    assert "尚未采集" in (body["reason"] or "")
    assert body["deduped"] == 0 and body["targets"] == {}


def test_有报告时字段与进度透传(as_doctor, monkeypatch, tmp_path):
    report = {
        "generated_at": "2026-10-13 20:00:00",
        "source": "business_db(diagnosis_results + feedback_records + dicom_images)",
        "total": 120,
        "deduped": 100,
        "undedupable": 3,
        "skipped_no_image": 17,
        "patient_count": 12,
        "patient_id_missing": 0,
        "dedup_reason": {"patient_image": 120},
        "doctor_labeled": 5,
        "dedup_basis": {"sop_instance_uid": 97, "none": 3},
        "targets": {"total": 700, "doctor_labeled": 50},
    }
    p = tmp_path / "langfuse_eval_set.json"
    p.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("UAI_EVAL_SET_REPORT", str(p))

    resp = as_doctor.get(_URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["deduped"] == 100 and body["total"] == 120
    assert body["doctor_labeled"] == 5 and body["undedupable"] == 3
    assert body["skipped_no_image"] == 17  # 无影像的判读显式计数（不静默丢样本）
    # A.62 批 3：患者维度必须透传 —— 「700 例」的真实刻度是不同患者数，不是样本条数
    assert body["patient_count"] == 12
    assert body["patient_id_missing"] == 0
    assert body["dedup_reason"] == {"patient_image": 120}
    assert body["targets"] == {"total": 700, "doctor_labeled": 50}
    # 进度比例（界面进度条直接消费）
    assert body["progress"]["samples"] == pytest.approx(100 / 700, abs=1e-4)
    assert body["progress"]["doctor_labeled"] == pytest.approx(0.1, abs=1e-4)
    assert body["dedup_basis"]["sop_instance_uid"] == 97
    # 契约字段集合（防漂移：界面按这些键渲染）
    assert {
        "available",
        "reason",
        "generated_at",
        "source",
        "total",
        "deduped",
        "undedupable",
        "doctor_labeled",
        "targets",
        "progress",
        "dedup_basis",
        "report_path",
    } <= set(body)


def test_报告损坏返回200不打500(as_doctor, monkeypatch, tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setenv("UAI_EVAL_SET_REPORT", str(p))

    resp = as_doctor.get(_URL)
    assert resp.status_code == 200, "报告损坏属可读降级，不能 500"
    body = resp.json()
    assert body["available"] is False
    assert "损坏" in (body["reason"] or "")
