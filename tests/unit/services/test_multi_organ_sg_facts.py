"""P2-10 合规守卫：多器官路径必须把临床事实透传给 SafetyGateway。

背景（2026-10-04 核实）：P2-10 原文写"多器官绕开 SafetyGateway"**与事实不符**
——多器官逐器官跑完整 ``run_diagnosis_pipeline``，SG 必然被调用。真实缺口是
**喂给 SG 的患者事实是空的**：``create_request`` 只传三字段，漏了
``clinical_text`` / ``patient_context`` / ``image_ids``，而这三项正是
``build_sg_output_data`` 组装禁忌检查输入的原料。漏传的后果不是"没检查"，
而是"检查在无事实的情况下运行"——只能走保守分支，等于合规检查失效。

本测试钉住这个不变量：多器官新建请求时**必须**带上这些字段（显式传入或
从同会话已有请求继承）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.multi_organ_orchestrator import _resolve_request_id

pytestmark = [pytest.mark.track_sec]


class _Repo:
    """记录 create_request 调用参数的假仓储。"""

    def __init__(self, created: list | None = None):
        self.created: list[dict] = created if created is not None else []

    async def create_request(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(request_id="req-test-0001")

    async def get_results_by_session(self, session_id):
        return []


def _row(**kw):
    base = {"request_id": "req-donor", "organ": "thyroid", "status": "completed"}
    base.update(kw)
    return SimpleNamespace(**base)


class TestClinicalFactsForwarded:
    @pytest.mark.asyncio
    async def test_explicit_facts_are_passed(self):
        repo = _Repo()
        await _resolve_request_id(
            diagnosis_repo=repo,
            existing=[],
            session_id="s1",
            organ=SimpleNamespace(value="thyroid"),
            response_tier=SimpleNamespace(value="realtime"),
            clinical_text="右叶低回声结节",
            patient_context={"age": 45, "conditions": ["甲亢"]},
            image_ids=["img_1", "img_2"],
        )
        got = repo.created[0]
        assert got["clinical_text"] == "右叶低回声结节"
        assert got["patient_context"]["conditions"] == ["甲亢"]
        assert got["image_ids"] == ["img_1", "img_2"]

    @pytest.mark.asyncio
    async def test_inherits_from_existing_request_in_session(self):
        """端点没显式传时，从同会话已有请求继承（医生已在单器官录过）。"""
        repo = _Repo()
        existing = [
            _row(clinical_text="既往有高血压病史", patient_context={"conditions": ["高血压"]},
                 image_ids=["img_x"]),
        ]
        await _resolve_request_id(
            diagnosis_repo=repo,
            existing=existing,
            session_id="s1",
            organ=SimpleNamespace(value="cardiac"),
            response_tier=SimpleNamespace(value="realtime"),
        )
        got = repo.created[0]
        assert got["clinical_text"] == "既往有高血压病史"
        assert got["patient_context"] == {"conditions": ["高血压"]}
        assert got["image_ids"] == ["img_x"]

    @pytest.mark.asyncio
    async def test_no_facts_anywhere_still_creates(self):
        """没有任何事实时不报错（不能因此让多器官不可用），但字段为 None。"""
        repo = _Repo()
        await _resolve_request_id(
            diagnosis_repo=repo,
            existing=[_row(clinical_text=None, patient_context=None, image_ids=None)],
            session_id="s1",
            organ=SimpleNamespace(value="liver"),
            response_tier=SimpleNamespace(value="realtime"),
        )
        got = repo.created[0]
        assert got["clinical_text"] is None
        assert got["patient_context"] is None
        assert got["image_ids"] is None

    @pytest.mark.asyncio
    async def test_reusable_request_skips_creation(self):
        """命中可重跑请求时不新建（也不该丢掉事实校验）。"""
        repo = _Repo()
        existing = [_row(request_id="req-reuse", organ="thyroid", status="failed")]
        rid = await _resolve_request_id(
            diagnosis_repo=repo,
            existing=existing,
            session_id="s1",
            organ=SimpleNamespace(value="thyroid"),
            response_tier=SimpleNamespace(value="realtime"),
            clinical_text="有事实",
        )
        assert rid == "req-reuse"
        assert repo.created == []

    @pytest.mark.asyncio
    async def test_failure_returns_none_not_raise(self):
        class _Boom:
            async def create_request(self, **kw):
                raise RuntimeError("db down")

        rid = await _resolve_request_id(
            diagnosis_repo=_Boom(),
            existing=[],
            session_id="s1",
            organ=SimpleNamespace(value="thyroid"),
            response_tier=SimpleNamespace(value="realtime"),
        )
        assert rid is None
