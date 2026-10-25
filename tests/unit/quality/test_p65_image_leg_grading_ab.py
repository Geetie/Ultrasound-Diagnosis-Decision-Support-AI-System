"""p65 预注册判据的守卫（**反证腿**：把判据改软/改硬都必须红）。

为什么单独守这几个纯函数：
`p65_image_leg_grading_ab.py` 的价值全在"判据跑前写死、跑完不许改"——
如果 `verdict()` 的阈值或顺序能被随手改掉，那份真机报告就失去意义。
所以这里既守**正向**（该达标的达标），也守**反向**（该拒的必须拒）。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

_spec = importlib.util.spec_from_file_location(
    "p65_ab", _ROOT / "scripts" / "audit" / "p65_image_leg_grading_ab.py"
)
p65 = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(p65)


def _rec(grade_level, says_unable=False, unparseable=None, leg=None):
    if unparseable is None:
        unparseable = grade_level is None
    return {
        "judgement": {
            "grade_raw": None if grade_level is None else f"TR{grade_level}",
            "grade_level": grade_level,
            "unparseable": unparseable,
            "says_unable": says_unable,
            "unable": bool(unparseable or says_unable),
        },
        "leg": leg or {"executed": True, "prompt_visible_image_chunks": 0},
        "wall_s": 10.0,
    }


def _arm(n=10, n_unable=4, grades=(3, 3, 4, 4, 5, 3), leg_ok=True,
         prompt_img=0):
    """构造一个臂：前 n_unable 例无法判断，其余按 grades 轮填。"""
    recs = []
    for i in range(n):
        if i < n_unable:
            recs.append(_rec(None, says_unable=True,
                             leg={"executed": leg_ok,
                                  "prompt_visible_image_chunks": prompt_img}))
        else:
            g = grades[i % len(grades)]
            recs.append(_rec(g, leg={"executed": leg_ok,
                                     "prompt_visible_image_chunks": prompt_img}))
    return p65.summarize_arm(recs)


# ----------------------------------------------------------------------
# classify_conclusion 口径
# ----------------------------------------------------------------------
def test_classify_unparseable_is_unable():
    j = p65.classify_conclusion("甲状腺 · 建议随访。")
    assert j["unparseable"] is True
    assert j["unable"] is True


def test_classify_graded_but_says_unable_counts_as_unable():
    """A.93 形态：标题 TR3、正文说"判不出来" → 必须算无法判断。"""
    j = p65.classify_conclusion(
        "甲状腺 · TR3 — 当前信息不足以进行 TI-RADS 分级，缺少必要的影像特征描述。"
    )
    assert j["grade_raw"] is not None
    assert j["unparseable"] is False
    assert j["says_unable"] is True
    assert j["unable"] is True


def test_classify_clean_graded_is_judgeable():
    j = p65.classify_conclusion(
        "甲状腺 · TR4 — 实性低回声，边界不规则，见微钙化，建议 FNA。"
    )
    assert j["grade_raw"] is not None
    assert j["unable"] is False


def test_classify_empty_is_unable():
    assert p65.classify_conclusion("")["unable"] is True


# ----------------------------------------------------------------------
# relative_drop / 基线为 0 不许静默当达标
# ----------------------------------------------------------------------
def test_relative_drop_normal():
    assert p65.relative_drop(0.40, 0.28) == pytest.approx(0.30, abs=1e-6)


def test_relative_drop_baseline_zero_not_treated_as_gain():
    assert p65.relative_drop(0.0, 0.0) == 0.0
    assert p65.relative_drop(0.0, 0.1) == 0.0


# ----------------------------------------------------------------------
# verdict：顺序与阈值
# ----------------------------------------------------------------------
def _summaries(off, on, off2):
    return {"off": off, "on": on, "off2": off2}


def test_verdict_gain_when_all_criteria_pass():
    off = _arm(n_unable=4)          # 40%
    on = _arm(n_unable=2)           # 20% → 降 50%
    off2 = _arm(n_unable=4)         # 控制一致
    code, facts = p65.verdict(_summaries(off, on, off2))
    assert code == "GAIN", facts
    assert facts["P2_gain"]["relative_drop"] == pytest.approx(0.5, abs=1e-6)


def test_verdict_no_gain_when_drop_below_30pct():
    """40% → 30% 只降 25% → NO_GAIN（阈值 30% 不许放软）。"""
    off = _arm(n_unable=4)
    on = _arm(n_unable=3)
    off2 = _arm(n_unable=4)
    code, facts = p65.verdict(_summaries(off, on, off2))
    assert code == "NO_GAIN"
    assert facts["P2_gain"]["passed"] is False


def test_verdict_control_instability_short_circuits_to_inconclusive():
    """两次 off 差 >20% → INCONCLUSIVE，**不许**降级成 NO_GAIN/GAIN。

    这是最关键的一条：控制不稳时任何 P2 差异都不可归因。
    """
    off = _arm(n_unable=2)   # 20%
    off2 = _arm(n_unable=5)  # 50% → 相对差 60%
    on = _arm(n_unable=1)    # 10%
    code, facts = p65.verdict(_summaries(off, on, off2))
    assert code == "INCONCLUSIVE_UNSTABLE_CONTROL"
    assert facts["P0_control"]["passed"] is False


def test_verdict_leg_unreachable_beats_no_gain():
    """腿没跑起来（比例 <0.9）→ LEG_UNREACHABLE，不是 NO_GAIN。"""
    off = _arm(n_unable=4)
    on = _arm(n_unable=4, leg_ok=False)
    off2 = _arm(n_unable=4)
    code, facts = p65.verdict(_summaries(off, on, off2))
    assert code == "LEG_UNREACHABLE"
    assert facts["P1_reachable"]["threshold_ok"] is False


def test_verdict_distribution_collapse_detected():
    """on 臂 100% 同一个分级 → DISTRIBUTION_COLLAPSE（哪怕无法判断率降了）。"""
    off = _arm(n_unable=4)
    # 全 TR4：single share = 1.0 > 0.80
    on_recs = [_rec(4) for _ in range(10)]
    on = p65.summarize_arm(on_recs)
    off2 = _arm(n_unable=4)
    code, facts = p65.verdict(_summaries(off, on, off2))
    assert code == "DISTRIBUTION_COLLAPSE"
    assert facts["P3_distribution"]["passed"] is False


def test_verdict_mean_level_shift_detected():
    """on 臂整体抬高一级以上 → 也算塌陷（P3 第三条）。"""
    off_recs = [_rec(2) for _ in range(10)]
    on_recs = [_rec(4) for _ in range(10)]
    code, facts = p65.verdict(_summaries(
        p65.summarize_arm(off_recs), p65.summarize_arm(on_recs),
        p65.summarize_arm(off_recs)))
    assert code == "DISTRIBUTION_COLLAPSE"
    assert facts["P3_distribution"]["mean_level_delta"] == pytest.approx(2.0)


def test_verdict_arms_incomplete():
    code, _ = p65.verdict({"off": _arm(), "on": _arm()})
    assert code == "ARMS_INCOMPLETE"


def test_verdict_reports_prompt_delivery_but_does_not_gate_on_it():
    """P1b 是诊断项：影像块没进 prompt 也要照样按 P2/P3 裁决，不许拿它当挡箭牌。"""
    off = _arm(n_unable=4)
    on = _arm(n_unable=2, prompt_img=0)   # 块没进 prompt
    off2 = _arm(n_unable=4)
    code, facts = p65.verdict(_summaries(off, on, off2))
    assert code == "GAIN"
    assert facts["P1b_prompt_delivery"]["cases_with_image_chunk_in_prompt"] == 0


# ----------------------------------------------------------------------
# 预注册阈值本身（防"跑完改阈值"）
# ----------------------------------------------------------------------
def test_pre_registered_thresholds_are_as_declared():
    assert p65.P2_MIN_RELATIVE_DROP == 0.30
    assert p65.P1_MIN_LEG_EXECUTED_RATIO == 0.90
    assert p65.P3_MAX_SINGLE_GRADE_SHARE == 0.80
    assert p65.P3_MAX_MEAN_LEVEL_DELTA == 0.75
    assert p65.P0_MAX_RELATIVE_DIFF == 0.20


def test_arms_are_exactly_three_as_designed():
    assert p65.ARM_NAMES == ("off", "on", "off2")


# ----------------------------------------------------------------------
# 证据链日志解析（真实 prompt 里到底喂了哪几块）
# ----------------------------------------------------------------------
def test_parse_evidence_chain_from_plain_log():
    """真机日志形态：structlog key=value，request_id 在 prompt_chunk_ids **之后**。"""
    log = (
        "2026-10-26 01:02:21 [info     ] evidence_chain                 "
        "organ=thyroid prompt_chunk_ids=['img_1', 'kb_x', 'kb_y'] "
        "prompt_chunks=3 request_id=req_a\n"
    )
    out = p65._parse_evidence_chain_log(log)
    assert out["req_a"] == ["img_1", "kb_x", "kb_y"]


def test_parse_evidence_chain_merges_multiple_lines_per_request():
    """同一 request_id 有多轮（首次 + CRAG 重检索）→ 取并集，不覆盖。"""
    log = (
        "evidence_chain organ=thyroid prompt_chunk_ids=['kb_a'] prompt_chunks=1 "
        "request_id=req_x\n"
        "evidence_chain organ=thyroid prompt_chunk_ids=['kb_b','img_9'] prompt_chunks=2 "
        "request_id=req_x\n"
    )
    out = p65._parse_evidence_chain_log(log)
    assert out["req_x"] == ["kb_a", "kb_b", "img_9"]


def test_parse_evidence_chain_ignores_recorded_round():
    """落库那一轮是 `recorded_ids` 不是 `prompt_chunk_ids` —— 不得混进 prompt 证据。"""
    log = (
        "evidence_chain organ=thyroid recorded_chunks=3 "
        "recorded_ids=['kb_z'] request_id=req_r\n"
    )
    assert p65._parse_evidence_chain_log(log) == {}


def test_parse_evidence_chain_from_json_log():
    log = (
        '{"event": "evidence_chain", "request_id": "req_b", '
        '"prompt_chunk_ids": ["kb_1"], "prompt_chunks": 1}\n'
    )
    out = p65._parse_evidence_chain_log(log)
    assert out["req_b"] == ["kb_1"]


def test_leg_events_distinguish_disabled_from_executed():
    """leg_disabled 的请求**不得**被算成"腿跑了"（旧版混合计数的那个 bug）。"""
    log = (
        "image_leg_attached bytes=0 ready=False request_id=req_off "
        "skip_reason=leg_disabled\n"
        "image_leg_skipped organ=thyroid reason=multimodal_disabled request_id=req_off\n"
        "image_leg_attached bytes=99676 ready=True request_id=req_on skip_reason=None\n"
    )
    ev = p65._parse_leg_events_by_request(log)
    assert ev["req_off"]["attached"] is True
    assert ev["req_off"]["attached_skip_reason"] == "leg_disabled"
    assert ev["req_on"]["attached_skip_reason"] is None


def test_leg_events_do_not_attribute_hits_without_request_id():
    """`image_leg_hits_admitted` 不带 request_id → 只能臂级统计，**不许**按顺序硬配。"""
    log = (
        "image_leg_attached bytes=1 ready=True request_id=req_a skip_reason=None\n"
        "image_leg_hits_admitted chunk_ids=['img_1'] count=1\n"
        "image_leg_attached bytes=1 ready=True request_id=req_b skip_reason=None\n"
        "image_leg_hits_admitted chunk_ids=['img_2','img_3'] count=2\n"
    )
    ev = p65._parse_leg_events_by_request(log)
    # 逐例记录里**没有** hits 字段（不可归因）
    assert "hits_admitted" not in ev["req_a"]
    assert "hits_admitted" not in ev["req_b"]
    # 臂级累计正确
    assert ev["__arm_level__"]["hits_admitted"] == 3
    assert ev["__arm_level__"]["admitted_chunk_ids"] == ["img_1", "img_2", "img_3"]


def test_summarize_arm_excludes_disabled_leg_from_executed():
    """summary.image_leg_executed 只数**真的执行了**的例，disabled 单独计数。"""
    recs = []
    for i in range(6):
        if i < 3:
            leg = {"executed": False, "attached_skip_reason": "leg_disabled",
                   "hits_admitted": 0, "prompt_visible_image_chunks": 0,
                   "evidence_source": "api.log:evidence_chain"}
        else:
            leg = {"executed": True, "attached_skip_reason": None,
                   "hits_admitted": 1, "prompt_visible_image_chunks": 0,
                   "evidence_source": "api.log:evidence_chain"}
        recs.append({"judgement": p65.classify_conclusion("甲状腺 · TR3 实性低回声"),
                     "leg": leg, "wall_s": 5.0})
    s = p65.summarize_arm(recs)
    assert s["image_leg_executed"] == 3
    assert s["image_leg_disabled_cases"] == 3
    # 逐例命中数**不可归因**（日志行无 request_id）→ 汇总里不出现该键
    assert "image_leg_hits_admitted_total" not in s


def test_summarize_arm_counts_handoff_guard_and_sg():
    """索要守卫兜底 / SG 禁忌命中必须**单独计数**（不许并进"无法判断"了事）。"""
    from app.agents.specialist.solicitation import HANDOFF_TEXT

    recs = [
        {"judgement": p65.classify_conclusion(HANDOFF_TEXT),
         "conclusion": HANDOFF_TEXT,
         "sg": {"contraindication_hit": True, "conclusion_suppressed": True},
         "leg": {}, "wall_s": 5.0},
        {"judgement": p65.classify_conclusion("甲状腺 · TR3 实性低回声"),
         "conclusion": "甲状腺 · TR3 实性低回声",
         "sg": {"contraindication_hit": False, "conclusion_suppressed": False},
         "leg": {}, "wall_s": 5.0},
    ]
    s = p65.summarize_arm(recs)
    assert s["handoff_guard_cases"] == 1
    assert s["sg_contraindication_hit"] == 1
    assert s["sg_conclusion_suppressed"] == 1
    # 兜底文案本身不含分级 → 计为 unable（口径不变）
    assert s["unable"] == 1


def test_attribution_block_reports_all_three_arms():
    off = _arm(n_unable=4)
    on = _arm(n_unable=4)
    off2 = _arm(n_unable=4)
    _code, facts = p65.verdict({"off": off, "on": on, "off2": off2})
    assert set(facts["attribution"]["handoff_guard_cases"]) >= {"off", "on", "off2"}
    assert set(facts["attribution"]["sg_contraindication_hit"]) >= {"off", "on", "off2"}


def test_scan_log_counts_leg_events():
    log = "image_leg_hits_admitted\n" * 3 + "image_leg_skipped x\n" * 2
    facts = p65._scan_log_for_leg(log)
    assert facts["image_leg_hits_admitted"] == 3
    assert facts["image_leg_skipped"] == 2
