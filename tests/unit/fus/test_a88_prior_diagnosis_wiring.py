"""A.88 A2 守卫：``prior_diagnosis`` 必须真的接到融合层（既往诊断不可达修复）。

## 缺陷（不是"没实现"，而是"永远拿不到"）

``ConsistencyChecker.check`` 的"与既往诊断对比"分支的唯一前提是
``request.prior_diagnosis is not None``（``app/fusion/consistency.py:623``），
而 ``diagnosis_pipeline._safe_fuse`` 组 ``FusionRequest`` 时**从不传**该字段
（``app/fusion/protocols.py:173`` 默认 ``None``）→ 这一层校验在生产上从未执行。
同时 ``DiagnosisRepository.get_results_by_session`` 只按 session 取，缺患者维度，
而"上一次诊断"通常发生在**另一个 session**（同患者复诊）→ 结构上也取不到。

## 修法（本文件逐条钉住）

1. 仓储：新增 ``get_previous_result_same_organ``，**SQL 级**限定
   患者/同器官/终态集合/排除本次 request_id（终态集合复用
   ``app.models.schemas.TERMINAL_RESULT_STATUSES``，不另写一份）；
2. 管线：在**读段**（DB 仍打开时）调用它；分级抽取复用单一事实源
   ``extract_grading_severest``；正文抽取复用 ``extract_findings_text``；
3. 组装 ``{"organ", "grade", "confidence"}`` → ``_safe_fuse(prior_diagnosis=...)``；
4. 取不到 → ``None`` + ``logger.info("pipeline_prior_diagnosis_absent", reason=...)``，
   **每个分支留自己的真实原因**（AGENTS.md 硬规则 8）。

反证腿：把 ``prior_diagnosis=prior_diagnosis`` 从 ``FusionRequest`` 里去掉 / 把
``_safe_load_prior_diagnosis`` 改成恒返回 None，本文件的用例即红。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.schemas import TERMINAL_RESULT_STATUSES
from app.repositories.diagnosis_repository import DiagnosisRepository
from app.services import diagnosis_pipeline as pipeline_module
from app.services.diagnosis_pipeline import _safe_load_prior_diagnosis

pytestmark = [pytest.mark.track_fus]


class _ResultRows:
    """``db.execute(...).scalars().all()`` 的最小替身。"""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    def __init__(self, rows: list) -> None:
        self.rows = rows
        self.statements: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _ResultRows(self.rows)


def _row(
    request_id: str = "req_prev",
    *,
    content: str | None = "分级: TR4 理由: 实性低回声 总分: 6",
    confidence: float | None = 0.8,
    nested: bool = False,
) -> SimpleNamespace:
    """一条历史诊断行（只带判定所需字段）。

    默认用**生产真实落库形状**（扁平 ``content``，见
    ``_fusion_result_to_dict`` 把正文写在 ``fusion_result["content"]``；
    真机抽 20 条带 fusion_result 的行，20/20 都是这个形状、0/20 有
    ``final_response`` 层）。嵌套形状仅在 ``nested=True`` 时用于兼容性腿 ——
    只测嵌套会掩盖"读路径恒空"那类缺陷（同事已在
    ``extract_findings_text`` 修过同款问题）。
    """
    if content is None:
        findings = {"status": "processing"}
    elif nested:
        findings = {"fusion_result": {"final_response": {"content": content}}}
    else:
        findings = {"fusion_result": {"content": content, "needs_review": False}}
    return SimpleNamespace(
        request_id=request_id,
        organ="thyroid",
        confidence=confidence,
        findings=findings,
    )


class TestTerminalStatusesSingleSource:
    """终态集合必须来自模型层的单一事实源，不能各写一份。"""

    def test_terminal_statuses_cover_all_result_states(self):
        assert set(TERMINAL_RESULT_STATUSES) == {
            "completed",
            "needs_review",
            "cancelled",
            "failed",
            "timeout",
        }

    def test_repository_uses_shared_constant(self):
        """反证腿：仓储若自己写死一份集合，needs_review 漏过会被抓。"""
        import inspect

        src = inspect.getsource(
            DiagnosisRepository.get_previous_result_same_organ
        )
        assert "TERMINAL_RESULT_STATUSES" in src, (
            "仓储没有复用 TERMINAL_RESULT_STATUSES —— 另写一份必然漂移"
        )
        assert "needs_review" not in src

    def test_query_filters_patient_organ_terminal_and_self(self):
        """SQL 级过滤：患者/同器官/终态/排除本次，四维缺一不可。"""
        from sqlalchemy.dialects import postgresql

        db = _FakeDB([])
        repo = DiagnosisRepository(db)  # type: ignore[arg-type]

        import asyncio

        asyncio.run(
            repo.get_previous_result_same_organ(
                patient_id="pat_1", organ="thyroid", exclude_request_id="req_new"
            )
        )

        assert db.statements, "没有发出查询"
        sql = str(
            db.statements[0].compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        assert "JOIN sessions" in sql
        assert "sessions.patient_id = 'pat_1'" in sql
        assert "diagnosis_results.organ = 'thyroid'" in sql
        assert "diagnosis_results.request_id != 'req_new'" in sql
        for status in TERMINAL_RESULT_STATUSES:
            assert f"'{status}'" in sql, f"终态 {status} 未进入 SQL 过滤"
        # processing 行不会被取到（它们没有可比对的正文）
        assert "'processing'" not in sql


class TestPriorDiagnosisAssembly:
    """组装语义：{organ, grade, confidence}，分级只走单一事实源。"""

    async def test_returns_grade_from_single_source(self, monkeypatch):
        """分级必须由 extract_grading_severest 产出（TR4 → '4'）。"""
        from app.inference import grading_definitions as grading_module

        seen: dict = {}
        real = grading_module.extract_grading_severest

        def _spy(text: str, organ: str):
            seen["text"] = text
            seen["organ"] = organ
            return real(text, organ)

        monkeypatch.setattr(
            grading_module, "extract_grading_severest", _spy
        )

        repo = MagicMock()
        repo.get_previous_result_same_organ = AsyncMock(
            return_value=[_row(content="分级: TR4 理由: 实性 总分: 6")]
        )

        prior = await _safe_load_prior_diagnosis(
            diagnosis_repo=repo,
            patient_id="pat_1",
            organ="thyroid",
            request_id="req_new",
        )

        assert prior is not None
        assert set(prior) >= {"organ", "grade", "confidence"}
        assert prior["organ"] == "thyroid"
        assert prior["grade"] == "4"
        assert prior["confidence"] == 0.8
        assert seen["organ"] == "thyroid"
        assert "TR4" in seen["text"]

    async def test_confidence_only_when_grade_unparsable(self):
        """有既往行但正文没分级 → 只用置信度比，并显式标注（不假装有分级）。"""
        repo = MagicMock()
        repo.get_previous_result_same_organ = AsyncMock(
            return_value=[_row(content="结节形态规则，建议随访", confidence=0.8)]
        )

        prior = await _safe_load_prior_diagnosis(
            diagnosis_repo=repo,
            patient_id="pat_1",
            organ="thyroid",
            request_id="req_new",
        )

        assert prior is not None
        assert prior["grade"] is None
        assert prior["confidence"] == 0.8

    async def test_skips_grade_less_row_to_find_graded_one(self):
        """最近一条没分级时往回翻（否则会把"有既往"误报成"没有既往"）。"""
        repo = MagicMock()
        repo.get_previous_result_same_organ = AsyncMock(
            return_value=[
                _row("req_newer", content="未见明显异常", confidence=0.5),
                _row("req_older", content="分级: TR3", confidence=0.7),
            ]
        )

        prior = await _safe_load_prior_diagnosis(
            diagnosis_repo=repo,
            patient_id="pat_1",
            organ="thyroid",
            request_id="req_new",
        )

        assert prior is not None
        assert prior["grade"] == "3"
        assert prior["source_request_id"] == "req_older"

    async def test_uses_repo_lookup_signature(self):
        repo = MagicMock()
        repo.get_previous_result_same_organ = AsyncMock(return_value=[_row()])

        await _safe_load_prior_diagnosis(
            diagnosis_repo=repo,
            patient_id="pat_1",
            organ="thyroid",
            request_id="req_new",
        )

        kwargs = repo.get_previous_result_same_organ.await_args.kwargs
        assert kwargs["patient_id"] == "pat_1"
        assert kwargs["organ"] == "thyroid"
        assert kwargs["exclude_request_id"] == "req_new"


class TestRealStoredShape:
    """形状腿：必须认**生产落库的真实形状**（扁平 content），否则恒取不到既往。

    真机事实（2026-10-25 抽查服务器 PG 最近 20 条带 fusion_result 的行）：
    20/20 是扁平 ``{"content": ..., "needs_review": ...}``，**0/20** 有
    ``final_response`` 这一层。只拿嵌套形状做用例会掩盖"读路径恒空"——
    同事已在 ``extract_findings_text`` 修过同款缺陷。

    反证腿：若抽取器退回"只认 final_response"，本用例即红（A2 在生产上
    又会退化成"永远没有既往"）。
    """

    async def test_flat_content_shape_is_understood(self):
        """真实正文（含标题 TR2 + 置信度 0.85）必须抽出主级 "2"。"""
        real_body = (
            "甲状腺 · TR2 — 分级: TR2 总分: 2 置信度: 0.85 理由: 实性低回声"
        )
        repo = MagicMock()
        repo.get_previous_result_same_organ = AsyncMock(
            return_value=[_row(content=real_body, confidence=0.85)]
        )

        prior = await _safe_load_prior_diagnosis(
            diagnosis_repo=repo,
            patient_id="pat_1",
            organ="thyroid",
            request_id="req_new",
        )

        assert prior is not None, "生产落库形状（扁平 content）没被认出来 → 既往恒不可达"
        # 关键：不是正文最后一个数字（0.85 → "85"），而是分级段里的 2
        assert prior["grade"] == "2", (
            f"取到了 {prior['grade']!r} —— 若为 '85' 说明又变成『取正文最后一个数字』"
        )
        assert prior["confidence"] == 0.85

    async def test_nested_shape_still_supported(self):
        """兼容腿：旧样本/``model_dump()`` 的嵌套形状不能被新逻辑抛弃。"""
        repo = MagicMock()
        repo.get_previous_result_same_organ = AsyncMock(
            return_value=[_row(content="分级: TR4", nested=True)]
        )

        prior = await _safe_load_prior_diagnosis(
            diagnosis_repo=repo,
            patient_id="pat_1",
            organ="thyroid",
            request_id="req_new",
        )

        assert prior is not None
        assert prior["grade"] == "4"

    async def test_flat_shape_grade_diff_is_zero_not_critical(self):
        """端到端语义：真实正文 vs 自己的主级 → 级差 0（不会误报 CRITICAL）。

        这是 A2 打开的那个"每次复诊都误报"的开关；这里钉住它现在是关的。
        """
        from app.fusion.consistency import _grade_diff

        real_body = (
            "甲状腺 · TR2 — 分级: TR2 总分: 2 置信度: 0.85 理由: 实性低回声"
        )
        repo = MagicMock()
        repo.get_previous_result_same_organ = AsyncMock(
            return_value=[_row(content=real_body, confidence=0.85)]
        )

        prior = await _safe_load_prior_diagnosis(
            diagnosis_repo=repo,
            patient_id="pat_1",
            organ="thyroid",
            request_id="req_new",
        )

        # 当前诊断正文与既往同为主级 2 → 级差 0（不是 83）
        assert _grade_diff(prior["grade"], real_body, "thyroid") == 0, (
            "既往主级 vs 当前正文的级差不为 0 —— 复诊会被误判成分级严重不一致"
        )


class TestAbsentReasonsAreReal:
    """取不到时必须留下**真实**原因（不许统一写死一个默认值）。"""

    async def _reasons(self, monkeypatch, **kwargs) -> list[str]:
        fake_logger = MagicMock()
        monkeypatch.setattr(pipeline_module, "logger", fake_logger)
        await _safe_load_prior_diagnosis(**kwargs)
        return [
            c.kwargs.get("reason")
            for c in fake_logger.info.call_args_list
            if c.args and c.args[0] == "pipeline_prior_diagnosis_absent"
        ]

    async def test_no_patient_id_reason(self, monkeypatch):
        reasons = await self._reasons(
            monkeypatch,
            diagnosis_repo=MagicMock(),
            patient_id=None,
            organ="thyroid",
            request_id="req_new",
        )
        assert reasons == ["no_patient_id"]

    async def test_no_organ_reason(self, monkeypatch):
        reasons = await self._reasons(
            monkeypatch,
            diagnosis_repo=MagicMock(),
            patient_id="pat_1",
            organ=None,
            request_id="req_new",
        )
        assert reasons == ["no_organ"]

    async def test_no_previous_reason(self, monkeypatch):
        repo = MagicMock()
        repo.get_previous_result_same_organ = AsyncMock(return_value=[])
        reasons = await self._reasons(
            monkeypatch,
            diagnosis_repo=repo,
            patient_id="pat_1",
            organ="thyroid",
            request_id="req_new",
        )
        assert reasons == ["no_previous"]

    async def test_unparsable_grade_reason(self, monkeypatch):
        repo = MagicMock()
        repo.get_previous_result_same_organ = AsyncMock(
            return_value=[_row(content="未见明显异常", confidence=None)]
        )
        reasons = await self._reasons(
            monkeypatch,
            diagnosis_repo=repo,
            patient_id="pat_1",
            organ="thyroid",
            request_id="req_new",
        )
        assert reasons == ["unparsable_grade"]

    async def test_lookup_failed_reason(self, monkeypatch):
        repo = MagicMock()
        repo.get_previous_result_same_organ = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        reasons = await self._reasons(
            monkeypatch,
            diagnosis_repo=repo,
            patient_id="pat_1",
            organ="thyroid",
            request_id="req_new",
        )
        assert reasons == ["lookup_failed"]

    async def test_reasons_are_not_a_single_constant(self, monkeypatch):
        """反证腿：各分支原因必须互不相同（写死一个默认值会在这里红）。"""
        no_prev = await self._reasons(
            monkeypatch,
            diagnosis_repo=_repo_empty(),
            patient_id="pat_1",
            organ="thyroid",
            request_id="req_new",
        )
        branch_1 = await self._reasons(
            monkeypatch,
            diagnosis_repo=MagicMock(),
            patient_id=None,
            organ="thyroid",
            request_id="req_new",
        )
        assert no_prev != branch_1


def _repo_empty() -> MagicMock:
    repo = MagicMock()
    repo.get_previous_result_same_organ = AsyncMock(return_value=[])
    return repo


class TestPlumbedIntoFusionRequest:
    """端到端接线：prior 必须真的落到 FusionRequest 上（这是缺陷本体）。"""

    async def test_safe_fuse_passes_prior_diagnosis(self, monkeypatch):
        import app.main as main_module
        from app.fusion.protocols import FusionResult
        from app.services.diagnosis_pipeline import _safe_fuse

        captured: dict = {}

        class _Engine:
            async def fuse(self, req):
                captured["prior"] = req.prior_diagnosis
                return FusionResult()

        monkeypatch.setattr(
            main_module.app.state, "fusion_engine", _Engine(), raising=False
        )

        prior = {"organ": "thyroid", "grade": "4", "confidence": 0.8}
        request = MagicMock()
        request.organ = "thyroid"

        await _safe_fuse(request, [], "rid", prior_diagnosis=prior)

        assert captured["prior"] == prior, (
            "prior_diagnosis 没有落到 FusionRequest —— 一致性『与既往比』永远不可达"
        )

    async def test_safe_fuse_defaults_to_none(self, monkeypatch):
        """反证腿：不传时必须是 None（不得编造一个空 dict 假装比过）。"""
        import app.main as main_module
        from app.fusion.protocols import FusionResult
        from app.services.diagnosis_pipeline import _safe_fuse

        captured: dict = {}

        class _Engine:
            async def fuse(self, req):
                captured["prior"] = req.prior_diagnosis
                return FusionResult()

        monkeypatch.setattr(
            main_module.app.state, "fusion_engine", _Engine(), raising=False
        )
        request = MagicMock()
        request.organ = "thyroid"

        await _safe_fuse(request, [], "rid")

        assert captured["prior"] is None

    def test_pipeline_call_site_passes_prior(self):
        """源码级守卫：管线的 _safe_fuse 调用点必须传 prior_diagnosis。"""
        import pathlib

        src = (
            pathlib.Path(pipeline_module.__file__).read_text(encoding="utf-8")
        )
        call_at = src.find("fusion_result = await _safe_fuse(")
        assert call_at != -1
        call = src[call_at : call_at + 700]
        assert "prior_diagnosis=prior_diagnosis" in call, (
            "管线调用 _safe_fuse 时不传 prior_diagnosis —— A2 的缺陷又回来了"
        )

    async def test_run_pipeline_hands_prior_end_to_end(self, monkeypatch):
        """行为级接线腿：读段取到的 prior 必须原样交给 `_safe_fuse`。

        源码守卫只能证明"写了那一行"，证明不了值真传下去（中间可能被覆盖成
        常量）。这里跑一遍完整的 ``run_diagnosis_pipeline``，把下游各环节
        patch 掉，只断言 ``_safe_fuse`` 实参里的 prior 就是读段返回的那份。
        """
        from app.fusion.protocols import FusionResult

        prior = {"organ": "thyroid", "grade": "3", "confidence": 0.7}
        captured: dict = {}

        async def _fake_load(**_kwargs):
            return prior

        async def _fake_fuse(**kwargs):
            captured.update(kwargs)
            return FusionResult()

        async def _fake_sg(**_kwargs):
            return ({"passed": True}, True)

        monkeypatch.setattr(pipeline_module, "_safe_load_prior_diagnosis", _fake_load)
        monkeypatch.setattr(
            pipeline_module, "_safe_dispatch", AsyncMock(return_value=[])
        )
        monkeypatch.setattr(pipeline_module, "_safe_fuse", _fake_fuse)
        monkeypatch.setattr(pipeline_module, "_safe_safety_check", _fake_sg)
        monkeypatch.setattr(
            pipeline_module, "_write_result_to_db", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(
            pipeline_module, "_safe_nodule_contour", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            pipeline_module, "_safe_similar_cases", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(pipeline_module, "_assess_routing", lambda _r: None)
        monkeypatch.setattr(
            pipeline_module,
            "_apply_rollout_gate",
            AsyncMock(side_effect=lambda _r, d: (d, None)),
        )
        monkeypatch.setattr(
            pipeline_module, "_read_retry_skip_self_rag", AsyncMock(return_value=False)
        )

        request_row = SimpleNamespace(
            request_id="req_run",
            session_id="sess_run",
            organ="thyroid",
            clinical_text="test",
            image_ids=[],
        )

        class _Repo:
            def __init__(self, _db):
                pass

            async def get_result(self, _rid):
                return request_row

        monkeypatch.setattr(pipeline_module, "DiagnosisRepository", _Repo)

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=MagicMock())
        cm.__aexit__ = AsyncMock(return_value=None)
        monkeypatch.setattr(
            pipeline_module, "AsyncSessionLocal", MagicMock(return_value=cm)
        )

        ok = await pipeline_module.run_diagnosis_pipeline("req_run")

        assert ok is True
        assert captured.get("prior_diagnosis") == prior, (
            "整条管线跑完，_safe_fuse 拿到的 prior_diagnosis 不是读段取到的那份"
        )


class TestPriorComparisonActuallyRuns:
    """行为腿：接线后一致性校验真的走到了"与既往比"。"""

    async def test_consistency_checker_compares_when_prior_present(self):
        from app.agents.protocols import AgentResponse
        from app.fusion.consistency import ConsistencyChecker
        from app.fusion.protocols import FusionRequest
        from app.models.schemas import Organ

        checker = ConsistencyChecker()
        request = FusionRequest(
            agent_responses=[
                AgentResponse(
                    agent_name="thyroid_specialist",
                    organ=Organ.THYROID,
                    content="分级: TR4",
                    confidence=0.9,
                )
            ],
            prior_diagnosis={"organ": "thyroid", "grade": "TR2", "confidence": 0.4},
        )

        result = await checker.check(request)

        assert result.compared_with_prior is True, (
            "传了 prior_diagnosis 却没有执行既往对比"
        )
        # 分级差 2 + 置信度差 0.5 → 至少 WARNING
        assert result.severity.value in ("warning", "critical")
