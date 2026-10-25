"""A.88 守卫：跨器官融合必须真的被喂到 ≥2 份独立结论。

## 这份守卫钉的是什么（不是"函数能跑"，而是"接线没断"）

2026-10-25 接线审计的结论是：设计文档的「1+N 并行 + 四层融合」在代码里**前提不成立** ——
`dispatcher.invoke()` 只调 `context.organ` 一个专科（`app/agents/dispatcher.py:185`），
而冲突检测的 4 类规则全部要求 `len(responses) >= 2`
（`app/fusion/conflict.py:287,313,335,384`），跨器官一致性同样要求 ≥2
（`app/fusion/consistency.py:656`）→ 单器官请求下这些防线**永远收不到输入**。

所以本守卫盯三件事：

1. **多器官收尾必须调用跨器官融合**（接在 `run_multi_organ_diagnosis` 的器官循环之后）；
2. **不足 2 份结论必须报 `insufficient_responses`**，不许把"只跑了 1 个器官"写成"无冲突"；
3. **冲突检测真的能被触发**（反证腿：两份分级不同的结论进来，必须报 GRADING_CONFLICT）。
   反证腿是这份守卫的核心 —— 没有它，一个"永远返回空"的实现也能让前两条全绿。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.protocols import AgentResponse
from app.fusion.conflict import ConflictDetector
from app.fusion.protocols import ConflictType
from app.models.schemas import Organ
from app.services import cross_organ_fusion as cof
from app.services.cross_organ_fusion import (
    CROSS_ORGAN_FINDINGS_KEY,
    build_cross_organ_responses,
    cross_organ_fusion,
)
from app.services.multi_organ_orchestrator import (
    MultiOrganOutcome,
    OrganRunResult,
    _apply_cross_organ_fusion,
)

# 注：pyproject 已设 `asyncio_mode = "auto"`，异步用例无需再打 pytestmark；
# 早先给整个模块加 `pytestmark = [pytest.mark.asyncio]` 会让**同步**用例也带上
# 该标记并刷 PytestWarning（实测 6 条），故不设模块级标记。


def _findings(organ: str, content: str, confidence: float = 0.9) -> dict:
    """构造一份形如管线的落库 findings（只保留本模块读的字段）。"""
    return {
        "organ": organ,
        "fusion_result": {
            "content": content,
            "final_confidence": confidence,
            "needs_review": False,
            "evidence_chunk_ids": [f"kb_{organ}_1"],
        },
    }


class TestBuildResponses:
    def test_reconstructs_one_response_per_concluded_organ(self):
        payloads = [
            {"organ": "thyroid", "findings": _findings("thyroid", "分级: TR3\n建议随访")},
            {"organ": "liver", "findings": _findings("liver", "分级: LR-4\n建议活检")},
        ]
        responses = build_cross_organ_responses(payloads)

        assert len(responses) == 2
        assert {r.organ for r in responses} == {Organ.THYROID, Organ.LIVER}
        assert responses[0].confidence == pytest.approx(0.9)
        assert responses[0].evidence_chunk_ids == ["kb_thyroid_1"]

    def test_skips_organs_without_conclusion(self):
        """没有正文的器官不得参与 —— 空正文会让关键词匹配产生假冲突。"""
        payloads = [
            {"organ": "thyroid", "findings": _findings("thyroid", "分级: TR3")},
            {"organ": "liver", "findings": {"status": "processing"}},
            {"organ": "cardiac", "findings": {}},
        ]
        responses = build_cross_organ_responses(payloads)

        assert [r.organ for r in responses] == [Organ.THYROID]

    def test_unknown_organ_is_dropped_not_guessed(self):
        responses = build_cross_organ_responses(
            [{"organ": "spleen", "findings": _findings("spleen", "分级: TR3")}]
        )
        assert responses == []


class TestCrossOrganFusion:
    async def test_insufficient_responses_is_reported_honestly(self):
        """只有 1 份结论 → 明确报 insufficient_responses，不许返回"无冲突"。"""
        summary = await cross_organ_fusion(
            session_id="s1",
            organ_results=[
                {"organ": "thyroid", "findings": _findings("thyroid", "分级: TR3")}
            ],
            fusion_engine=SimpleNamespace(),
        )

        assert summary["status"] == "insufficient_responses"
        assert summary["organ_count"] == 1
        assert "conflict" not in summary

    async def test_missing_engine_reports_unavailable(self):
        summary = await cross_organ_fusion(
            session_id="s1",
            organ_results=[
                {"organ": "thyroid", "findings": _findings("thyroid", "分级: TR3")},
                {"organ": "liver", "findings": _findings("liver", "分级: LR-4")},
            ],
            fusion_engine=None,
        )

        assert summary["status"] == "unavailable"
        assert summary["reason"] == "fusion_engine_unavailable"

    async def test_two_independent_conclusions_trigger_a_real_conflict(self):
        """反证腿：≥2 份独立结论进来，**能触发**的冲突类型必须真的报出来。

        这是「不是 bug 而是前提不成立」的可证伪面 —— 前提一旦成立（多器官会话），
        融合层就必须给出非空结论。若这里返回 has_conflict=False，
        说明检测器本身坏了（而不是"没有输入"）。

        为什么用 CONFIDENCE_CONFLICT 而不是 GRADING_CONFLICT：见下一条用例 ——
        分级冲突要求**同器官 ≥2 条**，而跨器官场景天生"一器官一条"，结构上
        不可达；置信度冲突是跨响应全对的，在多器官下真实可触发。
        """
        engine = SimpleNamespace(
            _conflict_detector=ConflictDetector(),
            _consistency_checker=_NoopConsistency(),
        )
        summary = await cross_organ_fusion(
            session_id="s1",
            organ_results=[
                {
                    "organ": "thyroid",
                    "findings": _findings("thyroid", "分级: TR3\n建议随访", 0.95),
                },
                {
                    "organ": "liver",
                    "findings": _findings("liver", "分级: LR-4\n建议活检", 0.30),
                },
            ],
            fusion_engine=engine,
        )

        assert summary["status"] == "ok"
        assert summary["conflict"]["has_conflict"] is True
        assert (
            ConflictType.CONFIDENCE_CONFLICT.value
            in summary["conflict"]["conflict_types"]
        )

    async def test_grading_conflict_is_structurally_unreachable_cross_organ(self):
        """诚实口径：分级冲突（GRADING_CONFLICT）在跨器官场景**不可能**触发。

        它要求"同一器官出现 ≥2 种分级码"（`app/fusion/conflict.py:287-300`），
        而跨器官输入每个器官只有一条结论（`build_cross_organ_responses` 一器官
        一条）。把这条钉住，是为了防止将来有人看到"多器官跑了冲突检测"就
        宣称"分级互检已生效" —— 那需要**同器官多智能体**，不是多器官。

        若哪天改成同器官并行多专科，这条会红：那是设计变更，应同步改叙述。
        """
        engine = SimpleNamespace(
            _conflict_detector=ConflictDetector(),
            _consistency_checker=_NoopConsistency(),
        )
        summary = await cross_organ_fusion(
            session_id="s1",
            organ_results=[
                {"organ": "thyroid", "findings": _findings("thyroid", "分级: TR3", 0.9)},
                {"organ": "liver", "findings": _findings("liver", "分级: LR-5", 0.9)},
                {"organ": "cardiac", "findings": _findings("cardiac", "LVEF 35%", 0.9)},
            ],
            fusion_engine=engine,
        )

        assert summary["status"] == "ok"
        assert (
            ConflictType.GRADING_CONFLICT.value
            not in summary["conflict"]["conflict_types"]
        )

    async def test_cross_organ_normal_versus_severe_is_flagged(self):
        """跨器官一致性：一器官"严重"而另一器官"正常"（都高置信）→ 至少 WARNING。

        ⚠️ 口径对齐（不是本模块自己的发明）：WARNING **不**触发 needs_review，
        只有 CRITICAL 才触发 —— 与 `app/fusion/layered_validator.py:131-137`
        的既有规则一致（那一层是"CRITICAL → review_reason=consistency_failed"）。
        本模块刻意不改这个阈值：另立一套就会让"单器官链路"与"跨器官链路"对
        同一份冲突给出不同结论。
        """
        from app.fusion.consistency import ConsistencyChecker

        engine = SimpleNamespace(
            _conflict_detector=ConflictDetector(),
            _consistency_checker=ConsistencyChecker(rag_service=None),
        )
        summary = await cross_organ_fusion(
            session_id="s1",
            organ_results=[
                {
                    "organ": "thyroid",
                    "findings": _findings("thyroid", "结节形态不规则，恶性征象，严重", 0.92),
                },
                {
                    "organ": "liver",
                    "findings": _findings("liver", "肝脏未见异常，正常", 0.92),
                },
            ],
            fusion_engine=engine,
        )

        assert summary["status"] == "ok"
        assert summary["consistency"]["severity"] in ("warning", "critical")
        assert summary["consistency"]["inconsistent_organ_pairs"]
        # WARNING → 按既有口径不转人工；CRITICAL 才转（口径单一事实源）
        if summary["consistency"]["severity"] == "warning":
            assert summary["needs_review"] is False
        else:
            assert summary["needs_review"] is True

    async def test_consistency_failure_does_not_block_conflict_result(self):
        """一致性校验抛错 → 冲突结果仍要返回（失败安全，不整体吞掉）。"""
        engine = SimpleNamespace(
            _conflict_detector=ConflictDetector(),
            _consistency_checker=_ExplodingConsistency(),
        )
        summary = await cross_organ_fusion(
            session_id="s1",
            organ_results=[
                {"organ": "thyroid", "findings": _findings("thyroid", "分级: TR3")},
                {"organ": "liver", "findings": _findings("liver", "分级: LR-4")},
            ],
            fusion_engine=engine,
        )

        assert summary["status"] == "ok"
        assert "consistency_check_failed" in summary["consistency"]["detail"]


class _NoopConsistency:
    async def check(self, request):
        from app.fusion.protocols import ConsistencyCheckResult

        return ConsistencyCheckResult(is_consistent=True, detail="noop")


class _ExplodingConsistency:
    async def check(self, request):
        raise RuntimeError("consistency exploded")


class _Repo:
    """内存仓储：够 _apply_cross_organ_fusion 用（get_result / update_findings）。"""

    def __init__(self, rows: dict[str, dict]):
        self.rows = rows
        self.updated: list[tuple[str, dict]] = []
        self.db = SimpleNamespace()

    async def get_result(self, request_id: str):
        findings = self.rows.get(request_id)
        if findings is None:
            return None
        return SimpleNamespace(request_id=request_id, findings=findings)

    async def update_findings(self, request_id: str, findings: dict) -> bool:
        self.updated.append((request_id, findings))
        self.rows[request_id] = findings
        return True


class TestOrchestratorWiring:
    async def test_single_concluded_organ_skips_fusion(self):
        """只跑了 1 个器官 → 不跑融合，且明确留 insufficient 口径。"""
        repo = _Repo({"req_t": _findings("thyroid", "分级: TR3")})
        results = [
            OrganRunResult(
                organ="thyroid",
                status="completed",
                request_id="req_t",
                confidence=0.9,
            ),
            OrganRunResult(organ="liver", status="failed", request_id="req_l"),
        ]

        summary = await _apply_cross_organ_fusion(
            diagnosis_repo=repo, session_id="s1", organ_results=results
        )

        assert summary["status"] == "insufficient_responses"
        assert repo.updated == []

    async def test_two_concluded_organs_write_back_each_request(self):
        """两个器官都产出结论 → 跨器官块写回**每一个**参与器官的 findings。"""
        repo = _Repo(
            {
                "req_t": _findings("thyroid", "分级: TR3"),
                "req_l": _findings("liver", "分级: LR-4"),
            }
        )
        results = [
            OrganRunResult(organ="thyroid", status="completed", request_id="req_t"),
            OrganRunResult(organ="liver", status="needs_review", request_id="req_l"),
        ]
        engine = SimpleNamespace(
            _conflict_detector=ConflictDetector(),
            _consistency_checker=_NoopConsistency(),
        )

        import app.services.multi_organ_orchestrator as orch

        original = orch._resolve_fusion_engine
        orch._resolve_fusion_engine = lambda: engine
        try:
            summary = await _apply_cross_organ_fusion(
                diagnosis_repo=repo, session_id="s1", organ_results=results
            )
        finally:
            orch._resolve_fusion_engine = original

        assert summary["status"] == "ok"
        assert {rid for rid, _ in repo.updated} == {"req_t", "req_l"}
        for _, findings in repo.updated:
            assert CROSS_ORGAN_FINDINGS_KEY in findings
            # 原有字段不得被覆盖
            assert findings["fusion_result"]["content"]

    async def test_findings_writeback_does_not_touch_status_or_confidence(self):
        """写回只加键：status / confidence 属于器官级判定，跨器官旁信息不得改它。"""
        original = _findings("thyroid", "分级: TR3")
        repo = _Repo({"req_t": original, "req_l": _findings("liver", "分级: LR-4")})
        results = [
            OrganRunResult(organ="thyroid", status="completed", request_id="req_t"),
            OrganRunResult(organ="liver", status="completed", request_id="req_l"),
        ]

        import app.services.multi_organ_orchestrator as orch

        original_resolver = orch._resolve_fusion_engine
        orch._resolve_fusion_engine = lambda: None
        try:
            await _apply_cross_organ_fusion(
                diagnosis_repo=repo, session_id="s1", organ_results=results
            )
        finally:
            orch._resolve_fusion_engine = original_resolver

        written = repo.rows["req_t"]
        assert written["organ"] == original["organ"]
        assert written["fusion_result"] == original["fusion_result"]


class TestOutcomeSurface:
    def test_outcome_serializes_cross_organ_block(self):
        outcome = MultiOrganOutcome(session_id="s1", session_status="completed")
        outcome.cross_organ = {"status": "ok", "organ_count": 2}

        payload = outcome.as_dict()

        assert payload["cross_organ"]["organ_count"] == 2

    def test_default_cross_organ_is_empty_dict_not_none(self):
        """序列化安全：默认必须是 {}（None 会让前端判空分支各写一套）。"""
        assert MultiOrganOutcome(session_id="s1", session_status="completed").as_dict()[
            "cross_organ"
        ] == {}


def test_min_responses_matches_conflict_detector_requirement():
    """单一事实源：本模块的 >=2 必须与冲突检测器四条规则的判据一致。

    若哪天冲突层放宽到"单响应自检"，这里会红 —— 提醒同步口径，
    而不是让两侧各说各话。
    """
    assert cof.MIN_RESPONSES == 2
    detector = ConflictDetector()
    single = [
        AgentResponse(agent_name="a", organ=Organ.THYROID, content="分级: TR3", confidence=0.9)
    ]
    assert detector.detect(single).has_conflict is False
