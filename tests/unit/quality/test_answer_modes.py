"""L2 判官「判什么」三口径的守卫（A.62 批 1 尾段）。

为什么值得钉住（都是真机形态普查 n=169 出来的）：
1. **标记必须来自单一事实源**：派生字段标记（分级/总分/置信度）由
   `app.inference.grading_definitions` 声明一次（与分级抽取共用）；若哪天 SSOT 改了词，
   这里必须立刻红，而不是悄悄少剔一个字段。
2. **必须打在真实形态上**：下面四条样本**逐字抄自真机评测集**（含旧英文降级串、
   markdown 形态、`**分级:**` 加粗形态）—— 按想象写正则的版本在这些形态上会静默失效。
3. **反证腿**：含派生标记 → 必须真的改写；纯叙述 → 必须原样返回；
   剔完为空 → 退回原文并记 `applied=False`（口径没生效必须可见）。
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from app.inference import grading_definitions as gd

REPO = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO / "scripts/langfuse/answer_modes.py"


def _load():
    spec = importlib.util.spec_from_file_location("_answer_modes_under_test", MODULE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)
    return mod


MODES = _load()

_ALL_SSOT_MARKERS = gd.FIELD_MARKERS_ANCHOR + gd.FIELD_MARKERS_INLINE + gd.FIELD_SEPARATORS

# ---------------------------------------------------------------------------
# 真机评测集的四条形态（逐字抄录，只截断长度，不改结构）
# ---------------------------------------------------------------------------

#: 最常见形态（157/169）：有「理由:」锚点
_REAL_TRIAGE = (
    "[thyroid] TR5 — # 输出 分级: TR2 总分: 2 置信度: 0.95 理由: 成分 1 + 回声 1 + 形态 0 + "
    "边缘 1 + 钙化 0 = 2 分 → TR2 证据: chunk_a1b2, chunk_c3d4 鉴别诊断: 1. 乳头状癌 | 0.60 | "
    "支持: 微钙化、低回声、纵横比>1 | 排除: 边界清晰 2. 结节性甲状腺肿 | 0.30 | 支持: 混合回声、"
    "边界清晰 | 排除: 微钙化 3. 亚急性甲状腺炎 | — | 支持: 片状低回声 | 排除: 无痛性结节 "
    "患者PATIENT_001: 该结节为良性，无需进一步检查。 # 输出 分级: TR5 总分: 14 置信度: 0.99 "
    "理由: 成分 3 + 回声 3 + 形态 3 + 边缘 2 + 钙化 3 = 14 分"
)

#: markdown 形态（4/169）：没有「理由:」锚点，字段带 ``**`` 与 ``###`` 标题
_REAL_MARKDOWN = (
    "[thyroid] TR5 — 根据提供的影像元数据和系统自动影像分析结果，以下是甲状腺结节的超声诊断分析： "
    "### 病灶特征 - **成分**：实性（需医生镜下确认） - **回声**：低回声（需医生镜下确认） "
    "### 分级与理由 - **评分**：成分 3 + 回声 2 + 形态 0 + 边缘 2 + 钙化 1 = **8 分 → TR5** "
    "- **置信度**：0.78 - **证据来源**：chunk_a1b2, chunk_c3d4 ### 理由 评分计算如下： "
    "- 成分：实性（3 分） - 回声：低回声（2 分）"
)

#: 心脏加粗形态（3/169）：字段是 ``**分级:**``，没有「理由:」锚点
_REAL_CARDIAC = (
    "[cardiac] LVEF=unknown, severity=normal — 根据提供的影像元数据和超声心动图知识库，当前信息不足以"
    "直接评估 LVEF 值。影像元数据主要提供了关于左心室局部区域的分割信息，但未直接提及 LVIDd、"
    "室壁厚度等关键参数。因此，无法依据 ASE 2015 标准进行 LVEF 评估。 "
    "**LVEF:** 无法计算 **分级:** 无法判断 **置信度:** 0.0 **证据:** 无 "
    "**鉴别诊断:** 1. 心肌病 | — | 支持: 局部区域异常 | 排除: 整体功能未提及"
)

#: 旧英文降级串（5/169）：本就没有派生字段，三口径应等价
_REAL_LEGACY = "[thyroid] llm_unavailable: InferenceError; manual review required"


def _derived_markers(text: str) -> list[str]:
    return [
        m.group(0)
        for m in MODES._FIELD_RE.finditer(text)
        if MODES._label_of(m.group(0)) in MODES._DERIVED_LABELS
    ]


# ---------------------------------------------------------------------------
# ① 单一事实源：标记只声明在 app 侧
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", gd.FIELD_MARKERS_ANCHOR + gd.FIELD_MARKERS_INLINE)
def test_segment_stop_or_anchor_covers_every_derived_marker(name: str) -> None:
    """app 侧的两个 SSOT 正则必须覆盖每个派生字段标记（改 SSOT 忘了改正则 → 红）。"""
    assert name in gd._SEGMENT_STOP.pattern or name in gd._EXPLICIT_GRADE_ANCHOR.pattern, (
        f"派生字段标记 {name} 不在 grading_definitions 的段边界定义里 —— "
        f"分级抽取与评测口径必须共用同一份标记"
    )


@pytest.mark.parametrize("name", gd.FIELD_SEPARATORS)
def test_segment_stop_covers_every_separator(name: str) -> None:
    assert name in gd._SEGMENT_STOP.pattern, f"叙述字段标记 {name} 丢失（段边界会走散）"


def test_derived_labels_are_exactly_the_app_ssot() -> None:
    """评测侧的"派生字段"集合必须由 app SSOT 推导，不得自带一套。"""
    expected = {n.lower() for n in gd.FIELD_MARKERS_ANCHOR + gd.FIELD_MARKERS_INLINE}
    assert expected == MODES._DERIVED_LABELS
    assert MODES._DERIVED_LABELS.isdisjoint({n.lower() for n in gd.FIELD_SEPARATORS}), (
        "同一个字段不能既算派生决策又算依据段"
    )


@pytest.mark.parametrize("name", _ALL_SSOT_MARKERS)
def test_script_regex_covers_every_ssot_marker(name: str) -> None:
    assert name in MODES._FIELD_RE.pattern, (
        f"SSOT 里的标记 {name} 没进评测侧切段正则（手抄一份必然走散）"
    )


# ---------------------------------------------------------------------------
# ② 真实形态：派生字段标记必须被剔干净，依据段必须活着
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [_REAL_TRIAGE, _REAL_MARKDOWN, _REAL_CARDIAC])
def test_real_shapes_have_derived_markers_to_begin_with(raw: str) -> None:
    """先证明样本"本来就有"派生字段 —— 否则下面的"剔干净"是空断言。"""
    assert _derived_markers(raw), "样本里没有派生字段标记，测试失去意义（样本抄错了）"


@pytest.mark.parametrize("raw", [_REAL_TRIAGE, _REAL_MARKDOWN, _REAL_CARDIAC])
def test_real_shapes_derived_markers_removed_and_evidence_kept(raw: str) -> None:
    out, applied = MODES.strip_derived_fields(raw)
    assert applied is True, "含派生字段却没被改写 —— 口径 C 静默失效"
    assert _derived_markers(out) == [], f"仍有派生字段标记：{_derived_markers(out)}"
    assert "理由" in out or "鉴别" in out, "依据段被一起吃掉了（剔除粒度太狠）"
    assert len(out) > 0


def test_legacy_english_degradation_is_untouched() -> None:
    """旧英文降级串本就没有派生字段 → 三口径等价（不许硬塞一次改写）。"""
    assert MODES.strip_derived_fields(_REAL_LEGACY) == (_REAL_LEGACY, False)


# ---------------------------------------------------------------------------
# ③ 反证腿：不误删 / 不空转
# ---------------------------------------------------------------------------


def test_plain_narrative_is_untouched() -> None:
    """纯叙述（只含「建议」这类依据标记、无头部）→ 原样返回。"""
    raw = "考虑良性结节，建议 6 个月后复查"
    assert MODES.strip_derived_fields(raw) == (raw, False)


def test_decision_head_is_dropped() -> None:
    """可读化头部（部位名 + 分级码）也是派生值 → 必须去掉。"""
    out, applied = MODES.strip_derived_fields("甲状腺 · TR3 — 实性低回声，边界清。")
    assert applied is True
    assert "TR3" not in out and out.startswith("实性")


def test_all_derived_falls_back_to_raw() -> None:
    """全是派生字段 → 退回原文并记 applied=False（不给判官空答案、不静默改样本量）。"""
    raw = "分级: TR3"
    assert MODES.strip_derived_fields(raw) == (raw, False)


def test_empty_input_is_passthrough() -> None:
    assert MODES.strip_derived_fields("") == ("", False)


# ---------------------------------------------------------------------------
# ④ answer_of：口径 B 的"静默退化"必须留痕
# ---------------------------------------------------------------------------


def test_raw_mode_never_rewrites() -> None:
    answer, applied = MODES.answer_of({"conclusion": _REAL_TRIAGE}, "raw")
    assert answer == _REAL_TRIAGE and applied is False


def test_reasoning_mode_applies_on_anchor() -> None:
    answer, applied = MODES.answer_of({"conclusion": _REAL_TRIAGE}, "reasoning")
    assert applied is True and answer.startswith("成分 1")


def test_reasoning_mode_reports_fallback_when_anchor_missing() -> None:
    """口径 B 的锚点缺失时必须如实记 applied=False（不许假装用了口径 B）。"""
    answer, applied = MODES.answer_of({"conclusion": _REAL_MARKDOWN}, "reasoning")
    assert applied is False and answer == _REAL_MARKDOWN
    answer2, applied2 = MODES.answer_of({"conclusion": _REAL_CARDIAC}, "reasoning")
    assert applied2 is False and answer2 == _REAL_CARDIAC


@pytest.mark.parametrize("raw", [_REAL_TRIAGE, _REAL_MARKDOWN, _REAL_CARDIAC])
def test_derived_stripped_mode_applies_on_all_three_shapes(raw: str) -> None:
    """口径 C 的卖点就是**不认字面锚点** → 三种形态都必须生效（B 在其中两种上退化）。"""
    _answer, applied = MODES.answer_of({"conclusion": raw}, "derived_stripped")
    assert applied is True


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        MODES.answer_of({"conclusion": "x"}, "口径Z")


def test_answer_modes_tuple_is_the_single_list() -> None:
    assert MODES.ANSWER_MODES == ("raw", "reasoning", "derived_stripped")


def test_judge_script_uses_shared_modes() -> None:
    """判官脚本必须 import 这份定义、并把生效条数写进报告（不许自己再写一套）。"""
    src = (REPO / "scripts/langfuse/judge_eval_set.py").read_text(encoding="utf-8")
    assert re.search(r"^from answer_modes import .*ANSWER_MODES.*answer_of", src, re.M), (
        "judge_eval_set.py 未复用 answer_modes（口径定义会走散）"
    )
    assert "mode_coverage" in src, "报告必须给出该口径的生效条数（口径静默退化要可见）"
