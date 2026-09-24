"""M2 自动升级编排服务单测。

分两层：
1. **纯逻辑层**（无需 DB）：高危标签收集、器官选择、防抖预留/释放、开关。
2. **编排层**：用假仓储/假 DB 会话替换模块内依赖，验证同意校验、落库、
   审计、入队与失败释放配额。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.schemas import ConsentStatus, NavigationAnnotation, ResponseTier, RiskFlag
from app.services import navigation_upgrade as upgrade_module
from app.services.navigation_upgrade import (
    NavigationUpgradeService,
    UpgradeDecision,
    collect_risk_labels,
)

pytestmark = pytest.mark.track_api


def _annotation(label="nodule", risk=None, suggested=None, confidence=0.9):
    return NavigationAnnotation(
        label=label,
        confidence=confidence,
        risk_flag=RiskFlag.HIGH if risk == "high" else None,
        suggested_action=suggested,
    )


# ----------------------------------------------------------------------
# 纯逻辑层
# ----------------------------------------------------------------------
class TestCollectRiskLabels:
    def test_collects_high_risk_labels(self):
        annotations = [
            _annotation("nodule", risk="high"),
            _annotation("other", risk="high"),
            _annotation("plain"),
        ]
        assert collect_risk_labels(annotations) == ["nodule", "other"]

    def test_deduplicates_preserving_order(self):
        annotations = [_annotation("a", risk="high"), _annotation("a", risk="high")]
        assert collect_risk_labels(annotations) == ["a"]

    def test_accepts_raw_string_flag_from_edge(self):
        """边缘端契约里 risk_flag 是裸字符串，必须同样识别。"""
        raw = SimpleNamespace(label="nodule", risk_flag="high")
        assert collect_risk_labels([raw]) == ["nodule"]

    def test_rejects_unexpected_flag_values(self):
        weird = [SimpleNamespace(label="nodule", risk_flag="medium")]
        assert collect_risk_labels(weird) == []

    def test_empty_input(self):
        assert collect_risk_labels([]) == []
        assert collect_risk_labels(None) == []


class TestReservation:
    def test_first_reservation_succeeds(self):
        service = NavigationUpgradeService(cooldown_seconds=60.0)
        assert service.try_reserve("s1") is None
        assert service.upgrade_count("s1") == 1
        assert service.remaining_budget("s1") == service.max_per_session - 1

    def test_cooldown_blocks_second_reservation(self):
        service = NavigationUpgradeService(cooldown_seconds=60.0, clock=lambda: 100.0)
        assert service.try_reserve("s1") is None
        assert service.try_reserve("s1") == "cooldown"

    def test_cooldown_expires(self):
        now = {"t": 100.0}
        service = NavigationUpgradeService(cooldown_seconds=60.0, clock=lambda: now["t"])
        assert service.try_reserve("s1") is None
        now["t"] += 61.0
        assert service.try_reserve("s1") is None
        assert service.upgrade_count("s1") == 2

    def test_session_limit(self):
        now = {"t": 0.0}
        service = NavigationUpgradeService(
            cooldown_seconds=0.0, max_per_session=2, clock=lambda: now["t"]
        )
        assert service.try_reserve("s1") is None
        now["t"] += 1.0
        assert service.try_reserve("s1") is None
        now["t"] += 1.0
        assert service.try_reserve("s1") == "session_limit"

    def test_sessions_are_isolated(self):
        service = NavigationUpgradeService(cooldown_seconds=60.0)
        assert service.try_reserve("s1") is None
        assert service.try_reserve("s2") is None

    def test_disabled_service_never_reserves(self):
        service = NavigationUpgradeService(enabled=False)
        assert service.try_reserve("s1") == "disabled"

    def test_release_restores_budget(self):
        service = NavigationUpgradeService(cooldown_seconds=60.0)
        service.try_reserve("s1")
        service.release("s1")
        assert service.upgrade_count("s1") == 0
        assert service.try_reserve("s1") is None

    def test_reset_single_and_all(self):
        service = NavigationUpgradeService(cooldown_seconds=60.0)
        service.try_reserve("s1")
        service.try_reserve("s2")
        service.reset("s1")
        assert service.upgrade_count("s1") == 0
        assert service.upgrade_count("s2") == 1
        service.reset()
        assert service.upgrade_count("s2") == 0

    def test_tier_and_defaults_from_settings(self):
        service = NavigationUpgradeService()
        assert service.tier in set(ResponseTier)
        assert service.cooldown_seconds >= 0
        assert service.max_per_session >= 1


class TestMaybeUpgradeShortCircuits:
    @pytest.mark.asyncio
    async def test_no_risk_flag_returns_without_touching_db(self):
        service = NavigationUpgradeService()
        decision = await service.maybe_upgrade(
            session_id="s1", frame_id="f1", annotations=[_annotation("plain")]
        )
        assert decision.triggered is False
        assert decision.reason == "no_risk_flag"
        assert service.upgrade_count("s1") == 0

    @pytest.mark.asyncio
    async def test_cooldown_returns_without_touching_db(self):
        service = NavigationUpgradeService(cooldown_seconds=60.0)
        service.try_reserve("s1")
        decision = await service.maybe_upgrade(
            session_id="s1",
            frame_id="f1",
            annotations=[_annotation("nodule", risk="high")],
        )
        assert decision.triggered is False
        assert decision.reason == "cooldown"
        assert decision.risk_labels == ["nodule"]

    @pytest.mark.asyncio
    async def test_disabled_returns_disabled(self):
        service = NavigationUpgradeService(enabled=False)
        decision = await service.maybe_upgrade(
            session_id="s1",
            frame_id="f1",
            annotations=[_annotation("nodule", risk="high")],
        )
        assert decision.reason == "disabled"

    @pytest.mark.asyncio
    async def test_exception_releases_reservation(self):
        service = NavigationUpgradeService(cooldown_seconds=60.0)

        async def _boom(**kwargs):
            raise RuntimeError("db down")

        service._perform_upgrade = _boom  # type: ignore[assignment]
        decision = await service.maybe_upgrade(
            session_id="s1",
            frame_id="f1",
            annotations=[_annotation("nodule", risk="high")],
        )
        assert decision.triggered is False
        assert decision.reason == "error:RuntimeError"
        # 失败不得虚耗配额
        assert service.upgrade_count("s1") == 0
        assert service.try_reserve("s1") is None


# ----------------------------------------------------------------------
# 编排层（假仓储 + 假 DB 会话）
# ----------------------------------------------------------------------
class _FakeDBSession:
    def __init__(self):
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def commit(self):
        self.commits += 1


def _install_fakes(
    monkeypatch,
    *,
    session=None,
    consent_status="granted",
    created_request_id="req_auto_1",
    audit_raises=False,
):
    created: list = []
    audited: list = []
    db = _FakeDBSession()

    class _SessionRepo:
        def __init__(self, _db):
            pass

        async def get_session(self, _session_id):
            return session

    class _ConsentRepo:
        def __init__(self, _db):
            pass

        async def get_latest_consent(self, _patient_id):
            if consent_status is None:
                return None
            return SimpleNamespace(status=consent_status)

    class _DiagnosisRepo:
        def __init__(self, _db):
            pass

        async def create_request(self, **kwargs):
            created.append(kwargs)
            return created_request_id

    class _AuditService:
        def __init__(self, _db):
            pass

        async def log_action(self, **kwargs):
            if audit_raises:
                raise RuntimeError("audit down")
            audited.append(kwargs)

    monkeypatch.setattr(upgrade_module, "AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(upgrade_module, "SessionRepository", _SessionRepo)
    monkeypatch.setattr(upgrade_module, "ConsentRepository", _ConsentRepo)
    monkeypatch.setattr(upgrade_module, "DiagnosisRepository", _DiagnosisRepo)
    monkeypatch.setattr(upgrade_module, "AuditService", _AuditService)
    return {"db": db, "created": created, "audited": audited}


class TestPerformUpgrade:
    @pytest.mark.asyncio
    async def test_happy_path_enqueues_and_audits(self, monkeypatch):
        session = SimpleNamespace(patient_id="pat_1", target_organs=["thyroid"])
        fakes = _install_fakes(monkeypatch, session=session)
        enqueued: list = []

        async def _enqueue(request_id):
            enqueued.append(request_id)
            return True

        service = NavigationUpgradeService(cooldown_seconds=0.0, enqueue=_enqueue)
        decision = await service.maybe_upgrade(
            session_id="s1",
            frame_id="f1",
            annotations=[_annotation("malignant_suspected", risk="high")],
        )
        assert decision.triggered is True
        assert decision.request_id == "req_auto_1"
        assert decision.organ == "thyroid"
        assert decision.risk_labels == ["malignant_suspected"]
        assert enqueued == ["req_auto_1"]
        assert fakes["created"][0]["organ"] == "thyroid"
        assert fakes["created"][0]["session_id"] == "s1"
        assert fakes["created"][0]["response_tier"] == service.tier
        assert fakes["audited"][0]["action"] == upgrade_module.AUDIT_ACTION
        assert fakes["audited"][0]["detail"]["risk_labels"] == ["malignant_suspected"]
        assert fakes["db"].commits >= 1

    @pytest.mark.asyncio
    async def test_uses_preferred_organ_when_in_session_organs(self, monkeypatch):
        session = SimpleNamespace(patient_id="pat_1", target_organs=["thyroid", "liver"])
        fakes = _install_fakes(monkeypatch, session=session)
        service = NavigationUpgradeService(cooldown_seconds=0.0, enqueue=AsyncMock(return_value=True))
        decision = await service.maybe_upgrade(
            session_id="s1",
            frame_id="f1",
            annotations=[_annotation("nodule", risk="high")],
            organ="liver",
            patient_id="pat_1",
        )
        assert decision.triggered is True
        assert fakes["created"][0]["organ"] == "liver"

    @pytest.mark.asyncio
    async def test_withdrawn_consent_blocks_upgrade(self, monkeypatch):
        session = SimpleNamespace(patient_id="pat_1", target_organs=["thyroid"])
        fakes = _install_fakes(
            monkeypatch, session=session, consent_status=ConsentStatus.WITHDRAWN.value
        )
        enqueue = AsyncMock(return_value=True)
        service = NavigationUpgradeService(cooldown_seconds=0.0, enqueue=enqueue)
        decision = await service.maybe_upgrade(
            session_id="s1",
            frame_id="f1",
            annotations=[_annotation("nodule", risk="high")],
            patient_id="pat_1",
        )
        assert decision.triggered is False
        assert decision.reason == "consent_withdrawn"
        assert fakes["created"] == []
        enqueue.assert_not_called()
        # 被同意拦截不消耗配额
        assert service.upgrade_count("s1") == 0

    @pytest.mark.asyncio
    async def test_missing_consent_record_blocks_upgrade(self, monkeypatch):
        session = SimpleNamespace(patient_id="pat_1", target_organs=["thyroid"])
        _install_fakes(monkeypatch, session=session, consent_status=None)
        service = NavigationUpgradeService(cooldown_seconds=0.0, enqueue=AsyncMock(return_value=True))
        decision = await service.maybe_upgrade(
            session_id="s1",
            frame_id="f1",
            annotations=[_annotation("nodule", risk="high")],
            patient_id="pat_1",
        )
        assert decision.triggered is False
        assert decision.reason == "consent_denied"

    @pytest.mark.asyncio
    async def test_session_not_found(self, monkeypatch):
        _install_fakes(monkeypatch, session=None)
        service = NavigationUpgradeService(cooldown_seconds=0.0, enqueue=AsyncMock(return_value=True))
        decision = await service.maybe_upgrade(
            session_id="missing",
            frame_id="f1",
            annotations=[_annotation("nodule", risk="high")],
        )
        assert decision.triggered is False
        assert decision.reason == "session_not_found"
        assert service.upgrade_count("missing") == 0

    @pytest.mark.asyncio
    async def test_empty_target_organs_without_hint(self, monkeypatch):
        session = SimpleNamespace(patient_id="pat_1", target_organs=[])
        _install_fakes(monkeypatch, session=session)
        service = NavigationUpgradeService(cooldown_seconds=0.0, enqueue=AsyncMock(return_value=True))
        decision = await service.maybe_upgrade(
            session_id="s1",
            frame_id="f1",
            annotations=[_annotation("nodule", risk="high")],
            patient_id="pat_1",
        )
        assert decision.triggered is False
        assert decision.reason == "missing_target_organ"

    @pytest.mark.asyncio
    async def test_enqueue_failure_reports_and_releases_budget(self, monkeypatch):
        session = SimpleNamespace(patient_id="pat_1", target_organs=["thyroid"])
        fakes = _install_fakes(monkeypatch, session=session)
        service = NavigationUpgradeService(
            cooldown_seconds=0.0, enqueue=AsyncMock(return_value=False)
        )
        decision = await service.maybe_upgrade(
            session_id="s1",
            frame_id="f1",
            annotations=[_annotation("nodule", risk="high")],
            patient_id="pat_1",
        )
        assert decision.triggered is False
        assert decision.reason == "enqueue_failed"
        assert decision.request_id == "req_auto_1"
        assert len(fakes["created"]) == 1
        assert service.upgrade_count("s1") == 0

    @pytest.mark.asyncio
    async def test_audit_failure_does_not_block_enqueue(self, monkeypatch):
        session = SimpleNamespace(patient_id="pat_1", target_organs=["thyroid"])
        _install_fakes(monkeypatch, session=session, audit_raises=True)
        enqueue = AsyncMock(return_value=True)
        service = NavigationUpgradeService(cooldown_seconds=0.0, enqueue=enqueue)
        decision = await service.maybe_upgrade(
            session_id="s1",
            frame_id="f1",
            annotations=[_annotation("nodule", risk="high")],
            patient_id="pat_1",
        )
        assert decision.triggered is True
        enqueue.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clinical_text_has_no_grading_terms(self, monkeypatch):
        """自动升级的临床文本不得含分级结论类术语（否则会被 SG 过滤/降级）。"""
        session = SimpleNamespace(patient_id="pat_1", target_organs=["thyroid"])
        fakes = _install_fakes(monkeypatch, session=session)
        service = NavigationUpgradeService(cooldown_seconds=0.0, enqueue=AsyncMock(return_value=True))
        await service.maybe_upgrade(
            session_id="s1",
            frame_id="f1",
            annotations=[_annotation("malignant_suspected", risk="high")],
            patient_id="pat_1",
        )
        text = fakes["created"][0]["clinical_text"]
        for forbidden in ("TI-RADS", "LI-RADS", "恶性", "良性", "分级", "可疑"):
            assert forbidden not in text


class TestUpgradeDecision:
    def test_as_dict(self):
        decision = UpgradeDecision(True, "b_plus_risk_flag", request_id="r1", organ="thyroid")
        payload = decision.as_dict()
        assert payload == {
            "triggered": True,
            "reason": "b_plus_risk_flag",
            "request_id": "r1",
            "organ": "thyroid",
            "risk_labels": [],
        }
