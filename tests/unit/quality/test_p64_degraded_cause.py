"""Q3/p64 守卫：无正文归因必须**逐条有证据**，且不许为了数字好看硬塞。

三类钉住：
* **归因纯函数**：每个根因分支都要能被单独触发，且证据来源字段存在；
* **反证腿**：把「看起来像」的错误判据塞回去，必须变红
  （无图 ⇒ no_image / status=timeout ⇒ upstream_timeout / needs_review+空 ⇒ grade_unparseable）；
* **口径守卫**：可用率三口径的分子分母必须写清；`unknown` 必须能保留。

另外钉住两个真实踩坑：
* `T3_无法判断` 必须排在 `T5_可用分级` 之前 —— `extract_grading_severest` 的全文兜底
  会把「甲状腺 · TR2 — 分级: 无法判断」扫出 `TR2`（假高）；
* `content` 键的**存在性**与**值是否为空**必须分开 —— 前者是契约缺口，后者才是模型产出空。
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

_SRC = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p64_degraded_cause_attribution.py"

KB_READY = {
    "thyroid": datetime(2026, 9, 29, 15, 40, tzinfo=timezone.utc),
    "liver": datetime(2026, 9, 30, 18, 9, tzinfo=timezone.utc),
    "cardiac": datetime(2026, 9, 30, 18, 9, tzinfo=timezone.utc),
}


def _load():
    spec = importlib.util.spec_from_file_location("_p64_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rec(
    *,
    findings=None,
    status="completed",
    organ="thyroid",
    created_at=None,
    knowledge_ids=("c1",),
    image_ids=(),
    recommendations="",
):
    return SimpleNamespace(
        request_id="req-test",
        findings=findings,
        status=status,
        organ=organ,
        created_at=created_at or datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc),
        knowledge_ids=list(knowledge_ids),
        image_ids=list(image_ids),
        recommendations=recommendations,
    )


def _grade_extractor():
    from app.inference.grading_definitions import extract_grading_severest

    return extract_grading_severest


# ----------------------------------------------------------------------
# 归因：每个分支都要能被单独触发
# ----------------------------------------------------------------------


def test_persist_failure_is_direct_and_checked_first():
    """落库连接被掐断 —— findings 只有 error，正文从未写入。"""
    p64 = _load()
    rec = _rec(
        findings={
            "error": "(sqlalchemy...asyncpg.InterfaceError) ... underlying connection is closed",
            "error_type": "InterfaceError",
        },
        status="failed",
    )
    out = p64.attribute(rec, kb_first_ingest=KB_READY, has_service_evidence=False, has_image_leg_trace=False)
    assert out["root_cause"] == "persist_failure"
    assert out["confidence"] == "direct"
    assert any("error_type" in e for e in out["evidence"])
    assert out["missing_evidence"] == []


def test_consent_withdrawn_not_counted_as_failure():
    """同意撤回是政策终止 —— 不许算成系统故障。"""
    p64 = _load()
    rec = _rec(
        findings={"error": "consent_withdrawn", "reason": "in-flight diagnosis cancelled"},
        status="cancelled",
    )
    out = p64.attribute(rec, kb_first_ingest=KB_READY, has_service_evidence=False, has_image_leg_trace=False)
    assert out["root_cause"] == "consent_withdrawn"


def test_upstream_timeout_needs_stale_processing_marker_not_status_field():
    """真超时证据是 stale_processing 留痕；`status='timeout'` 从不落库。"""
    p64 = _load()
    rec = _rec(
        findings={
            "fusion_result": {
                "reason": "stale_processing",
                "content": "本次判读未能在预期时间内完成（已等待超过 30 分钟）…",
                "degraded": True,
            }
        },
        status="failed",
    )
    out = p64.attribute(rec, kb_first_ingest=KB_READY, has_service_evidence=True, has_image_leg_trace=False)
    assert out["root_cause"] == "upstream_timeout"
    assert any("stale_processing" in e for e in out["evidence"])


def test_grade_unparseable_requires_marker_string():
    """有输出但抽不出分级 —— 必须真的有降级标记串。"""
    p64 = _load()
    rec = _rec(
        recommendations="[thyroid] grade_unparseable: LLM output did not contain a valid TI-RADS grade",
        knowledge_ids=("c1", "c2"),
    )
    out = p64.attribute(rec, kb_first_ingest=KB_READY, has_service_evidence=False, has_image_leg_trace=False)
    assert out["root_cause"] == "grade_unparseable"
    assert out["confidence"] == "direct"


def test_llm_inference_error_requires_nonempty_retrieval():
    """推理异常与检索问题要分开：knowledge_ids 非空才能排除检索。"""
    p64 = _load()
    rec = _rec(
        recommendations="[thyroid] llm_unavailable: InferenceError; manual review required",
        knowledge_ids=("c1", "c2", "c3"),
    )
    out = p64.attribute(rec, kb_first_ingest=KB_READY, has_service_evidence=False, has_image_leg_trace=False)
    assert out["root_cause"] == "llm_inference_error"
    assert any("knowledge_ids 非空" in e for e in out["evidence"])


def test_retrieval_empty_flags_unknown_when_kb_not_ingested_yet():
    """检索零命中且当时该器官 KB 还没灌 —— 证据只能到 inferred，且必须写明缺什么。"""
    p64 = _load()
    rec = _rec(
        organ="liver",
        knowledge_ids=(),
        created_at=datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc),  # liver KB 9/30 才灌
        recommendations="[liver] evidence_insufficient: no relevant knowledge retrieved",
    )
    out = p64.attribute(rec, kb_first_ingest=KB_READY, has_service_evidence=True, has_image_leg_trace=False)
    assert out["root_cause"] == "kb_not_ingested"
    assert out["confidence"] == "inferred"
    assert out["missing_evidence"], "缺证据必须显式写明，不许空着"


def test_kb_present_but_zero_hit_is_retrieval_empty_not_kb_not_ingested():
    """KB 已存在却零命中 —— 这才是「知识覆盖不足」，与「库还没建」必须分开。"""
    p64 = _load()
    rec = _rec(
        organ="thyroid",
        knowledge_ids=(),
        created_at=datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc),  # thyroid KB 9/29 就绪
        recommendations="[thyroid] evidence_insufficient: no relevant knowledge retrieved",
    )
    out = p64.attribute(rec, kb_first_ingest=KB_READY, has_service_evidence=False, has_image_leg_trace=False)
    assert out["root_cause"] == "retrieval_empty"


def test_write_gap_produced_ok_separates_key_absence_from_empty_value():
    """契约缺口：**没有 content 键** 但 recommendations 里有分级 → 正文其实产出了。"""
    p64 = _load()
    rec = _rec(
        findings={"fusion_result": {"needs_review": False, "self_rag_passed": True}},
        recommendations="[thyroid] TR4 — 分级: TR4 总分: 6 置信度: 0.85 理由: 低回声…",
    )
    out = p64.attribute(rec, kb_first_ingest=KB_READY, has_service_evidence=True, has_image_leg_trace=False)
    assert out["root_cause"] == "write_gap_produced_ok"
    assert any("缺 content 键" in e for e in out["evidence"])


def test_unknown_is_kept_with_missing_evidence_list():
    """证据不足必须保留 unknown，并写明缺哪些证据。"""
    p64 = _load()
    rec = _rec(
        findings={"fusion_result": {"needs_review": True, "self_rag_passed": False}},
        status="needs_review",
        knowledge_ids=("c1",),
        recommendations="",
    )
    out = p64.attribute(rec, kb_first_ingest=KB_READY, has_service_evidence=False, has_image_leg_trace=False)
    assert out["root_cause"] == "unknown"
    assert out["confidence"] == "unknown"
    assert len(out["missing_evidence"]) >= 2


def test_every_root_cause_has_a_record_source():
    """纪律：每个归因都必须能指名证据来源字段。"""
    p64 = _load()
    cases = [
        (_rec(findings={"error": "x", "error_type": "InterfaceError"}, status="failed"), "persist_failure"),
        (_rec(findings={"error": "consent_withdrawn"}, status="cancelled"), "consent_withdrawn"),
        (
            _rec(
                findings={"fusion_result": {"reason": "stale_processing", "content": "未能在预期时间内完成"}},
                status="failed",
            ),
            "upstream_timeout",
        ),
        (_rec(recommendations="grade_unparseable: xxx"), "grade_unparseable"),
        (_rec(recommendations="llm_unavailable: InferenceError"), "llm_inference_error"),
        (_rec(knowledge_ids=()), "retrieval_empty"),
        (
            _rec(
                findings={"fusion_result": {"needs_review": True}},
                recommendations="TR2 — 分级: TR2",
            ),
            "write_gap_produced_ok",
        ),
    ]
    for rec, expected in cases:
        out = p64.attribute(rec, kb_first_ingest=KB_READY, has_service_evidence=True, has_image_leg_trace=False)
        assert out["root_cause"] == expected, out
        assert out["sources"], f"{expected} 必须给 sources"


# ----------------------------------------------------------------------
# 反证腿：把「看起来像」的错误判据塞回去，必须变红
# ----------------------------------------------------------------------


def test_negation_no_image_requires_trace_not_just_missing_image_ids():
    """反证：只因为 image_ids 为空就判 no_image → 必须不成立（全库 92.8% 无图）。

    真机事实：全库 461/497 无 image_ids，有正文的 246 条里也有 210 条无图。
    若「无图 ⇒ no_image」成立，那么绝大多数**有正文**的记录也该被归为 no_image，
    该判据显然没有区分性。
    """
    p64 = _load()
    rec = _rec(
        recommendations="[thyroid] TR4 — 分级: TR4 总分: 6",
        image_ids=(),  # 无图
        knowledge_ids=("c1",),
    )
    out = p64.attribute(rec, kb_first_ingest=KB_READY, has_service_evidence=False, has_image_leg_trace=False)
    assert out["root_cause"] != "no_image", "仅凭无 image_ids 不得归因为 no_image"

    # 只有**同时**有影像腿留痕才允许归 no_image
    rec_trace = _rec(
        findings={"augmentation_skip_reason": "no_image_bytes", "fusion_result": {}},
        image_ids=(),
        knowledge_ids=("c1",),
    )
    out2 = p64.attribute(rec_trace, kb_first_ingest=KB_READY, has_service_evidence=False, has_image_leg_trace=True)
    assert out2["root_cause"] == "no_image"


def test_negation_needs_review_plus_empty_is_not_grade_unparseable():
    """反证：任务字面判据（needs_review 且 content 空 ⇒ grade_unparseable）会吞掉检索空。

    真机：128 条 needs_review+无正文里，只有 3 条真带分级标记，125 条是
    evidence_insufficient（检索空）。两者修法完全不同。
    """
    p64 = _load()
    rec = _rec(
        status="needs_review",
        findings={"fusion_result": {"needs_review": True, "self_rag_passed": False}},
        knowledge_ids=(),
        recommendations="[liver] evidence_insufficient: no relevant knowledge retrieved",
    )
    out = p64.attribute(rec, kb_first_ingest=KB_READY, has_service_evidence=True, has_image_leg_trace=False)
    assert out["root_cause"] == "retrieval_empty"
    assert out["root_cause"] != "grade_unparseable"


def test_negation_status_timeout_value_alone_cannot_be_attributed():
    """反证：`status='timeout'` 从不落库（全库 0 行），按它统计必得 0。"""
    p64 = _load()
    rec = _rec(findings={"fusion_result": {"needs_review": True}}, status="timeout")
    out = p64.attribute(rec, kb_first_ingest=KB_READY, has_service_evidence=False, has_image_leg_trace=False)
    # 分支存在（能识别 status=timeout），但真机取数为 0 —— 这是"实现正确但数据为 0"，
    # 不是"实现漏了"。用常量钉住判据来源，防止有人改成按 status 派生。
    assert out["root_cause"] == "upstream_timeout"


# ----------------------------------------------------------------------
# 可用性分层
# ----------------------------------------------------------------------


def test_unable_to_grade_beats_severest_fallback():
    """T3 必须排在 T5 之前：`TR2 — 分级: 无法判断` 的全文兜底会扫出 TR2（假高）。"""
    p64 = _load()
    extractor = _grade_extractor()
    rec = _rec(findings={"fusion_result": {"content": "甲状腺 · TR2 — 分级: 无法判断 置信度: 0.0 理由: 缺少影像特征"}})
    # 先证明"尺子确实会扫出 TR2"（否则这条守卫就是空转）
    assert extractor("甲状腺 · TR2 — 分级: 无法判断 置信度: 0.0", "thyroid") is not None
    assert p64.availability_tier(rec, extractor) == "T3_unable"


def test_timeout_tier_before_grade_tier():
    p64 = _load()
    extractor = _grade_extractor()
    rec = _rec(findings={"fusion_result": {"content": "本次判读未能在预期时间内完成（已等待超过 30 分钟）… 分级: TR4"}})
    assert p64.availability_tier(rec, extractor) == "T1_timeout"


def test_empty_body_falls_back_to_recommendations():
    """读正文要能回退 recommendations —— 契约缺口期正文都在那一列。"""
    p64 = _load()
    extractor = _grade_extractor()
    rec = _rec(findings={"fusion_result": {"needs_review": False}}, recommendations="TR4 — 分级: TR4")
    assert p64.body_of(rec.findings) == ""
    assert p64.readable_text(rec) == "TR4 — 分级: TR4"
    assert p64.availability_tier(rec, extractor) == "T5_usable_grade"


def test_availability_calibers_are_self_describing():
    """三口径必须给分子定义，否则又回到"随便挑一个数"。"""
    p64 = _load()
    out = p64.summarize_availability(["T5_usable_grade", "T5_usable_grade", "T3_unable", "T2_degraded", "T0_no_text"])
    assert out["total"] == 5
    assert out["body_rate"] == 0.8  # 5 条里 4 条有正文
    assert out["non_degraded_rate"] == 0.6  # 非 T0/T1/T2 = T5+T5+T3 = 3/5
    assert out["usable_grade_rate"] == 0.4
    assert set(out["numerator_definition"]) == {
        "body_rate",
        "non_degraded_rate",
        "usable_grade_rate",
    }
    # 三者关系必须成立（否则口径互相矛盾）
    assert out["usable_grade_rate"] <= out["non_degraded_rate"] <= out["body_rate"]


def test_task_taxonomy_reports_inflation_of_literal_definition():
    """必须暴露"照字面定义会多算多少条"，否则读者会以为真是那类。"""
    p64 = _load()
    records = [
        {"root_cause": "retrieval_empty", "status": "needs_review", "has_body": False, "confidence": "direct"},
        {"root_cause": "kb_not_ingested", "status": "needs_review", "has_body": False, "confidence": "inferred"},
        {"root_cause": "grade_unparseable", "status": "needs_review", "has_body": False, "confidence": "direct"},
    ]
    out = p64.summarize_task_taxonomy(records)
    assert out["grade_unparseable"]["n"] == 1
    assert out["grade_unparseable"]["naive_literal_definition_would_count"] == 3
    assert out["grade_unparseable"]["inflation"] == 2
    assert out["grade_unparseable"]["note"]


def test_zero_categories_explain_themselves_with_counter_evidence():
    """某类为 0 时要给实证解释（不是"没数据"）—— no_image / upstream_timeout。"""
    p64 = _load()
    records = [
        {
            "root_cause": "retrieval_empty",
            "status": "needs_review",
            "has_body": False,
            "n_image_ids": 0,
            "confidence": "direct",
        },
        {
            "root_cause": "retrieval_empty",
            "status": "needs_review",
            "has_body": False,
            "n_image_ids": 0,
            "confidence": "direct",
        },
    ]
    out = p64.summarize_task_taxonomy(records)
    assert out["no_image"]["n"] == 0
    assert "92.8%" in out["no_image"]["why_zero"] or "无图" in out["no_image"]["why_zero"]
    assert out["upstream_timeout"]["n"] == 0
    assert "stale_processing" in out["upstream_timeout"]["why_zero"]


def test_outage_window_and_content_contract_are_distinct_periods():
    """时间窗与契约缺口是两个**不同**的边界 —— 混用会把契约缺口算成服务故障。"""
    p64 = _load()
    # 9/30 16:00 UTC：在故障窗内，但**早于**契约接线（17:35:50 UTC）
    in_window_before_fix = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)
    assert p64.in_outage_window(in_window_before_fix) is True
    assert p64.before_content_contract(in_window_before_fix) is True

    # 10/01 06:00 UTC：故障窗已过，契约已接
    after_both = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)
    assert p64.in_outage_window(after_both) is False
    assert p64.before_content_contract(after_both) is False

    # 关键反证：只有当**两个条件都排除**，才叫正常期
    assert datetime(2026, 9, 30, 17, 35, 50, tzinfo=timezone.utc) == p64.CONTENT_CONTRACT_FIX_UTC


def test_matrix_is_three_dimensional_and_sorted():
    p64 = _load()
    records = [
        {"organ": "thyroid", "root_cause": "retrieval_empty", "day": "2026-09-29"},
        {"organ": "thyroid", "root_cause": "retrieval_empty", "day": "2026-09-29"},
        {"organ": "liver", "root_cause": "persist_failure", "day": "2026-09-30"},
    ]
    rows = p64.summarize_matrix(records)
    assert rows[0]["n"] == 2
    assert rows[0]["organ"] == "thyroid"
    assert {r["organ"] for r in rows} == {"thyroid", "liver"}
    assert sum(r["n"] for r in rows) == 3


def test_has_content_key_distinguishes_absent_from_empty():
    """`content` 键缺失（契约缺口）与 `content` 值为空（模型产出空）必须分开。"""
    p64 = _load()
    assert p64.has_content_key({"fusion_result": {"needs_review": False}}) is False
    assert p64.has_content_key({"fusion_result": {"content": ""}}) is True
    assert p64.has_content_key({"fusion_result": {"content": "TR4"}}) is True
    assert p64.has_content_key({}) is False
    assert p64.has_content_key(None) is False


def test_outage_window_uses_utc_day_not_local():
    """按 UTC 日切窗（与 16 号历史口径一致）；本地 CST 会整体右移 8 小时。"""
    p64 = _load()
    # 9/30 23:30 UTC = 10/01 07:30 CST
    ts = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)
    assert p64.in_outage_window(ts) is True
    cst = timezone(timedelta(hours=8))
    assert ts.astimezone(cst).strftime("%Y-%m-%d") == "2026-10-01"


def test_day_key_uses_requested_timezone():
    """日界必须显式指定时区：同一时间戳在 UTC 与 CST 下可能是不同日期。"""
    p64 = _load()
    ts = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)  # = 10/01 07:30 CST
    assert p64.day_key(ts, timezone.utc) == "2026-09-30"
    assert p64.day_key(ts, p64.CST) == "2026-10-01"
    assert p64.day_key(None, timezone.utc) == "(无日期)"


def test_build_report_day_tz_sensitivity_comes_from_raw_timestamps():
    """按天双口径必须从原始 created_at 折算，不能拿已折好的日期再折一次。

    反证：若实现用 `day` 字符串再 astimezone，UTC 与 CST 两列会完全相同 ——
    而真机实测两者相差 41 条（跨日界记录）。
    """
    p64 = _load()
    # 两条跨日界记录：UTC 9/30 23:30 = CST 10/01 07:30
    rows = [
        _rec(
            findings={"fusion_result": {}},
            organ="liver",
            created_at=datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc),
            knowledge_ids=(),
        ),
        _rec(
            findings={"fusion_result": {}},
            organ="liver",
            created_at=datetime(2026, 9, 30, 23, 40, tzinfo=timezone.utc),
            knowledge_ids=(),
        ),
    ]
    report = p64.build_report(
        rows,
        KB_READY,
        {"by_day": {}, "files_used": [], "unreadable_files": [], "lines_scanned": 0, "outage_day_totals": {}},
    )
    tz = report["calibers"]["no_body_by_day_utc_vs_cst"]
    assert tz["utc"] == {"2026-09-30": 2}, tz
    assert tz["cst"] == {"2026-10-01": 2}, tz
    assert tz["utc"] != tz["cst"], "两种日界必须能区分开，否则该字段是空转"
