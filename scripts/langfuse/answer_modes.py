"""L2 判官「判什么」的三种口径（**单一事实源**）。

* ``raw``              —— 结论全文（口径 A）
* ``reasoning``        —— 只判「理由:」之后的临床叙述（口径 B）
* ``derived_stripped`` —— 剔除系统派生决策字段后再判（口径 C）

为什么必须有第三个（2026-10-14 真机形态普查，n=169）
-----------------------------------------------------
口径 B 依赖「理由:」这个**字面锚点**。真实评测集 169 条结论的形态分布是：

    157 条 有「理由:」锚点（B 生效）
      5 条 ``[thyroid] llm_unavailable: …; manual review required``（旧英文降级串，无锚点）
      4 条 markdown 形态（``### 理由`` + `` — ``，没有 ``理由:`` 锚点）
      3 条 ``[cardiac] LVEF=unknown, severity=normal — … **分级:** 无法判断 …``（加粗字段，无锚点）

即 **B 在这 12 条上静默退化成 A**（原样返回全文），而台账里写着"第三种口径（剔除派生字段）"
却从来没有实现过 —— 这正是本项目「零静默」纪律针对的形态：口径悄悄变了，数字照报。

口径 C 的做法
-------------
**不认字面锚点，认生产侧的字段标记**：派生决策字段的标记由
:mod:`app.inference.grading_definitions` 声明一次（与分级抽取共用同一份定义），
标记属于「系统派生出的决策」（分级 / 总分 / 置信度）的段一律剔除，
承载"依据"的段（理由 / 证据 / 鉴别诊断 / 患者解释 / 建议）原样保留。

剔除的**粒度刻意保守**：只删「标记 + 紧邻的那个值 token」，**不删到下一个标记为止**。
理由是格式不统一（真机 169 条里有 markdown 形态 `**置信度**：0.78 - **证据来源**：…`），
"删到下一个标记"会把整段依据一起吃掉（实测模拟：`### 理由 评分计算如下` 不是标记
→ 置信度段会一直删到文末）。保守粒度下最坏只留下 `分 → TR2` 这种评分推导碎片，
它与口径 B（判「理由:」之后全文）的保留范围一致。

**已知边界（不粉饰）**：
* 头部规则（器官代码 / 部位名 + 分级码）会整体去掉结论头，包括可读化心脏头里的
  LVEF 与严重程度；但**正文里**的测量字段（如 ``**LVEF:** 无法计算``）不属于
  "派生决策"，保留待判 —— 它是"依据不足"的如实呈现，不是分类结论；
* 含 ``分级`` 但不带冒号的标题（如 markdown ``### 分级与理由``）不是字段标记，原样保留。

每条样本都要报告该口径**是否真的改写过答案**（``applied``），报告里给出生效条数：
锚点缺失时留痕，不假装用了该口径。
"""
from __future__ import annotations

import re

from app.inference.grading_definitions import (
    FIELD_MARKERS_ANCHOR,
    FIELD_MARKERS_INLINE,
    FIELD_SEPARATORS,
)

#: 三种口径名（CLI ``--answer-mode`` 的取值）。
ANSWER_MODES: tuple[str, ...] = ("raw", "reasoning", "derived_stripped")

#: 口径 B 的锚点：结构化输出里「理由:」之后才是临床叙述。
_REASON_MARKERS: tuple[str, ...] = ("理由:", "理由：")

#: 「系统派生出的决策」= 段起锚点 + 段内派生字段（**都来自 app 侧 SSOT，不许在本文件复制字面量**）。
_DERIVED_LABELS: frozenset[str] = frozenset(
    name.lower() for name in FIELD_MARKERS_ANCHOR + FIELD_MARKERS_INLINE
)

#: 任意字段标记 —— 用来切段（``**分级:**`` 这种 markdown 强调也要认）。
_FIELD_RE = re.compile(
    r"\*{0,2}(?:"
    + "|".join(FIELD_MARKERS_ANCHOR + FIELD_MARKERS_INLINE + FIELD_SEPARATORS)
    + r")\*{0,2}\s*[:：]",
    re.IGNORECASE,
)

#: 生产侧结论头部的**唯二形态**（真机实证）：
#:   * ``[thyroid] TR5 — …``              专科 ``AgentResponse.content``，英文方括号器官代码
#:   * ``甲状腺 · TR3 — …``                ``readable.graded_content``
#:   * ``心脏 · 左室射血分数（LVEF）62% · 严重程度：中度 — …``  ``readable.cardiac_content``
_HEAD_SEP = re.compile(r"\s+[—–]\s+")
_HEAD_MARK = re.compile(r"^\s*(?:\[[a-z_]+\]|[^—–\n]{0,16}·)")

#: 派生字段「值」的结束边界：第一个空白。派生字段的值都是单 token（``TR2`` / ``0.95`` /
#: ``无法判断`` / ``62%``），用空白收口比"删到下一个标记"安全得多（见模块 docstring）。
_VALUE_GAP = re.compile(r"\s")


def _label_of(marker: str) -> str:
    """``**分级:**`` / ``**置信度**：`` → ``分级`` / ``置信度``。

    真机的 markdown 强调**两种顺序都有**（``**分级:`` 与 ``**置信度**：``）→ 不能只
    ``strip("*")`` 再 ``rstrip(":：")``（那样 ``**置信度**：`` 会留下尾巴 ``**``，
    标签识别不到 → 该字段被当成"依据"保留）。一律剔掉空白/星号/两种冒号。
    """
    return re.sub(r"[\s*:：]", "", marker).lower()


def _drop_decision_head(text: str) -> str:
    """去掉结论头部（器官代码 / 部位名 + 分级码）—— 它们都是系统派生值。"""
    if not _HEAD_MARK.match(text):
        return text
    m = _HEAD_SEP.search(text)
    return text[m.end() :] if m else text


def _tidy(text: str) -> str:
    """合并空白并去掉剔除后残留的分隔符/悬空标点。"""
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip("—–-·,，;；、。 \t")


def strip_derived_fields(text: str) -> tuple[str, bool]:
    """剔除派生决策字段 → ``(剔除后的正文, 是否真的改写过)``。

    删除粒度 = 「标记 + 紧邻的那个值 token」（值到下一个字段标记或**第一个空白**为止）：
    派生字段的值都是短 token（``TR2`` / ``3`` / ``0.95`` / ``无法判断`` / ``62%``），
    而"删到下一个标记为止"在 markdown 形态上会把依据整段吃掉（见模块 docstring）。

    改写结果是空串时**退回原文**并把 ``applied`` 记成 ``False``（口径没生效必须可见，
    不能让判官拿到空答案而静默改变样本量）。
    """
    if not text or not text.strip():
        return text, False

    marks = list(_FIELD_RE.finditer(text))
    cuts: list[tuple[int, int]] = []
    for m in marks:
        if _label_of(m.group(0)) not in _DERIVED_LABELS:
            continue
        limit = next((x.start() for x in marks if x.start() >= m.end()), len(text))
        # 跳过「标记:」与值之间的空白，再从值本身找结束边界（否则「分级: TR3」会被切掉整段值）
        i = m.end()
        while i < limit and text[i].isspace():
            i += 1
        ws = _VALUE_GAP.search(text, i, limit)
        cuts.append((m.start(), ws.start() if ws else limit))

    if not cuts:
        cleaned = _tidy(_drop_decision_head(text))
        return (cleaned, cleaned != text) if cleaned else (text, False)

    # 保留区间 = [0, cut0.start) ∪ [cut_i.end, cut_{i+1}.start) ∪ … ∪ [cutN.end, len)
    # ⚠️ 相邻 cut 之间必须用**前一个 cut 的 end 到后一个 cut 的 start**；
    # 写成 [prev_end, end] 会把后一个派生字段一起留下（守卫当场抓到过）。
    parts = [_drop_decision_head(text[: cuts[0][0]])]
    prev_end = cuts[0][1]
    for start, end in cuts[1:]:
        parts.append(text[prev_end:start])
        prev_end = end
    parts.append(text[prev_end:])
    cleaned = _tidy("".join(parts))
    return (cleaned, True) if cleaned else (text, False)


def answer_of(item: dict, mode: str) -> tuple[str, bool]:
    """取「被判的答案」→ ``(答案, 该口径是否真的生效)``。

    ``raw`` 按定义不改写（``applied=False``）；``reasoning`` 在锚点缺失时**如实**记
    ``applied=False``（不再假装"用了口径 B"）。
    """
    text = (item.get("conclusion") or "").strip()
    if mode == "raw":
        return text, False
    if mode == "reasoning":
        for marker in _REASON_MARKERS:
            if marker in text:
                tail = text.split(marker, 1)[1].strip()
                return (tail, True) if tail else (text, False)
        return text, False
    if mode == "derived_stripped":
        return strip_derived_fields(text)
    raise ValueError(f"未知口径：{mode}（可选 {ANSWER_MODES}）")


__all__ = ["ANSWER_MODES", "answer_of", "strip_derived_fields"]
