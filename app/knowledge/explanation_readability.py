"""患者解释的**可读性与结构**检查（B3，按科普写作的公认标准）。

## 为什么这部分不需要医生

原来把 B3（话术质量评审集）当成"必须医生打分"，是**把两件事混为一谈**：

| 问题 | 需要谁 |
|---|---|
| "这段话说得对不对"（医学正确性） | **医生** |
| "这段话说得清不清楚"（可读性/结构/有没有吓人） | **不需要医生**，有公认标准 |

科普写作有成熟的**研究验证过的判据**，不是主观口味：

- **CDC Clear Communication Index**（20 项，基于 Plain Writing Act 2010 与
  National Action Plan to Improve Health Literacy）：
  One Main Message / Common words / Active voice / Call to Action /
  What We Know and Don't Know / Explain the nature of the risk /
  Probability words and visuals …
  https://www.cdc.gov/ccindex/tool/index.html
- **AMA / NIH 患者教育材料标准**：六年级阅读水平、短句、第二人称。
- **中文对应做法**：`《「健康中国」行动》健康科普规范`、中华医学会科普分会
  「通俗、准确、不夸大、给出下一步」四原则。

这些都能**离线确定性计算**，不需要任何模型、不需要医生、不需要花钱。

## 本模块检查什么（全部可计算）

| 项 | 判据 | 依据 |
|---|---|---|
| `no_main_message` | 首句未给出结论性信息 | CDC Part A #1「One Main Message」 |
| `too_many_ideas` | 一句话内并列分句 ≥4 个 | CDC Part A #9「Chunks」 |
| `long_sentence` | 单句 > 40 字 | 中文科普通行上限（六年级阅读水平） |
| `jargon_density` | 专业术语密度过高且首次出现未解释 | CDC Part A #7「Common words」 |
| `passive_or_vague` | 通篇无第二人称/无行动指引 | CDC Part B #12「Behavioral Recommendations」 |
| `no_next_step` | 未给出下一步（复查/就诊/观察） | CDC Part A #5「Call To Action」 |
| `risk_not_explained` | 提到风险但未说明"这意味着什么" | CDC Part D #18「Explain the nature of the risk」 |
| `uncertainty_hidden` | 有不确定性却用确定语气 | CDC Part A #11「What We Know and Don't Know」 |
| `scare_language` | 恐吓性措辞 | 医学伦理：不得以恐吓促成依从 |

## 边界（必须写明）

`passed=True` **只代表"结构上说得清楚"**，**不代表医学正确**。
医学正确性仍需医生 review 或（将来）金标准对比。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
#: 中文句末标点
_SENT_SPLIT_RE = re.compile(r"[。！？；\n]+")
#: 并列连接词（一句话里堆太多说明"想法太多"）
#: 并列连接词。
#: A.170：**顿号不算并列**——"多喝水、少熬夜、定期复查"是一条建议里的并列项，
#: 不是"多个想法塞一起"。只认逗号与显式连接词。
_COORD_RE = re.compile(r"[，,]|以及|还有|另外|同时")
#: 第二人称 / 行动指向（给读者的直接指引）
_SECOND_PERSON_RE = re.compile(r"您|你")
#: 下一步指引。
#:
#: A.170：必须排除**否定形式**——「放心吧，不用复查」含"复查"但语义相反，
#: 首版据此判为"有下一步"（黄金集里唯一一条漏报就是这个）。
_NEXT_STEP_RE = re.compile(
    r"(?<!不用)(?<!无需)(?<!不必)(?<!没必要)(?<!不用再)"
    r"(复查|复诊|随诊|就诊|就医|来院|进一步检查|穿刺|观察|随访|咨询医生|问医生"
    # A.170 补充：口语化的随访安排也是"下一步"，黄金集里 3 条误报都栽在这
    # （"明年再查一次""一年后再查一次""一年查一次就行"）。
    r"|再查一次|再查一回|查一次|随时来"
    # 注意：**不能裸加「体检」**——"这次体检发现结节"里的体检是既往事件不是下一步
    # （A.170 实测：裸加导致 pe-gold-031 漏报）。只认"按…体检安排"这类指向未来的表述。
    r"|(?:按|常规|单位).{0,6}体检)"
)
#: 不确定表达
_UNCERTAIN_RE = re.compile(r"可能|或许|大概|不排除|倾向于|提示|考虑|需结合|有待")
#: 绝对/确定语气
_CERTAIN_RE = re.compile(r"一定|必然|肯定|就是|确诊|百分之百|绝对")
#: 恐吓性措辞。
#:
#: A.170 扩充（2026-11-08 用 50 条人工黄金集实测）：首版仅 7 词，7 条恐吓样本只抓到 1 条
#: （召回 0.14）。真实恐吓不是说"致命/等死"，而是"就晚了""要有心理准备""就是癌""后果不堪设想"。
#: 扩充后召回 1.00 且零误报（详见 data/eval/v2/readability_checker_defects_20261108.md）。
_SCARE_RE = re.compile(
    r"致命|活不了|等死|癌变|恶化很快|再不.*就|后悔"
    r"|就晚了|来不及|要有心理准备|后果不堪设想|不敢想"
    r"|越来越严重|麻烦大了|出大问题|就是癌|晚期了"
)
#: **真正需要解释**的影像术语（患者一般不认识的）。
#:
#: 刻意**不含**「结节」「良性」「恶性」这类已被大众媒体普及的词——
#: 把它们算作行话会导致"提醒患者有个结节"也被判为太专业（实测踩过：
#: 「您这个结节是良性的。建议您半年后复查」被误报 jargon_density）。
#: 判据：只有影像科内部描述词才算行话。
_JARGON_TERMS = (
    "低回声", "高回声", "无回声", "等回声", "混合回声",
    "TI-RADS", "TI RADS", "LI-RADS", "TR1", "TR2", "TR3", "TR4", "TR5",
    "纵横比", "微钙化", "粗钙化", "点状强回声",
    "CDFI", "血流信号", "弹性成像", "超声造影",
    "实性结节", "囊性结节", "边界欠清", "形态不规则", "占位性病变",
)
#: 判断"术语是否被解释过"的提示词
_EXPLAIN_MARKERS = ("是指", "意思是", "也就是", "通俗地说", "简单说", "所谓的")
#: 单句长度上限（中文字符）
MAX_SENTENCE_CHARS = 40
#: 一句内并列分句上限
MAX_COORD_CLAUSES = 4
#: 术语密度上限（每 100 字里允许的未解释术语数）
MAX_JARGON_PER_100 = 3.0


@dataclass(frozen=True)
class ReadabilityIssue:
    code: str
    detail: str


@dataclass(frozen=True)
class ReadabilityReport:
    evaluated: bool
    passed: bool
    issues: tuple[ReadabilityIssue, ...]
    metrics: dict[str, float]

    def codes(self) -> tuple[str, ...]:
        return tuple(i.code for i in self.issues)


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]


def _cjk_len(text: str) -> int:
    return len(_CJK_RE.findall(text))


#: 术语附近多大范围内出现解释标记才算"解释过"
_JARGON_WINDOW = 40


def _jargon_unexplained(text: str) -> list[str]:
    """返回**未被就近解释**的术语。

    A.170 修正（结构性缺陷）：首版只要全文任意位置出现一次"意思是"，
    **所有**术语都算解释过 —— 实测「TI-RADS 4b、边界欠清、纵横比>1、微钙化，
    这里说的意思是：请复查」会被整段放行，而四个术语一个都没解释。

    改为**就近窗口**：解释标记必须出现在该术语 ±40 字内才算解释过。
    """
    marker_positions = [
        m.start() for marker in _EXPLAIN_MARKERS for m in re.finditer(re.escape(marker), text)
    ]
    # 一个解释标记只能"抵扣"**一个**术语。
    #
    # 为什么：句子里有 4 个术语、只出现一次"意思是"时，若允许"就近即算解释"，
    # 那一个标记会把四个术语全洗白（实测 `TI-RADS 4b、边界欠清、纵横比>1、微钙化，
    # 这里说的意思是：请复查` 被判零未解释术语）。真实解释是"「低回声」也就是看起来
    # 比周围暗一些"——**一个标记配一个术语**。
    used: set[int] = set()
    unexplained: list[str] = []
    for term in _JARGON_TERMS:
        for m in re.finditer(re.escape(term), text):
            candidates = [
                i for i, pos in enumerate(marker_positions)
                if i not in used and abs(pos - m.start()) <= _JARGON_WINDOW
            ]
            if not candidates:
                unexplained.append(term)
                break
            used.add(candidates[0])
    return unexplained


def check_readability(text: str | None) -> ReadabilityReport:
    """对患者解释做结构/可读性检查（纯函数，无副作用、无模型依赖）。

    Args:
        text: 患者解释正文。

    Returns:
        :class:`ReadabilityReport`。``evaluated=False`` 表示没有可评内容
        （空 / None），**不等于通过**。
    """
    if not text or not text.strip():
        return ReadabilityReport(False, False, (), {})

    raw = text.strip()
    sentences = _split_sentences(raw)
    issues: list[ReadabilityIssue] = []

    # 1) 主信息：**前两句**内应给出结论性信息。
    # A.170：首版只查第一句 + 词表 10 词，8 条误报——"先打招呼再给结论"
    # （"您好，这次检查发现…"）被判成"没有主信息"。中文医患沟通里先问候是常态。
    _head = "；".join(sentences[:2]) if sentences else raw
    if not re.search(
        r"考虑|提示|属于|分级|类|良性|恶性|正常|未见|发现|建议"
        r"|符合|偏向|结论|叫做|大概|多数|没看到|没见|范围内|不用|不是|没有|说明",
        _head,
    ):
        issues.append(ReadabilityIssue(
            "no_main_message", f"前两句内未给出结论：{_head[:24]}"
        ))

    # 2) 一句话里想法太多
    for s in sentences:
        clauses = len(_COORD_RE.findall(s)) + 1
        if clauses >= MAX_COORD_CLAUSES + 1:
            issues.append(ReadabilityIssue(
                "too_many_ideas", f"一句里有 {clauses} 个并列分句：{s[:24]}"
            ))
            break

    # 3) 长句
    # A.170：首版「>40 字即长句」有 7 条误报——把"一个概念讲得长"误判为"多个概念塞一起"。
    # 实测：fail 组长句平均有 6~9 个逗号，pass 组仅 1~4 个。
    # 改为双条件：>40 字 **且**（逗号≥3 **或** >55 字）。
    # 单一主句的解释型长句（如"您这个结节在超声上表现为边界清楚形态规则的低回声区
    # 域没有任何可疑特征"）会被放行。纯字数会打坏 test_long_sentence_detected（60 字零逗号），
    # 所以 >55 字仍判长句作为兜底。
    long_sents = [
        s for s in sentences
        if _cjk_len(s) > MAX_SENTENCE_CHARS
        and (s.count("，") + s.count(",") >= 3 or _cjk_len(s) > 55)
    ]
    if long_sents:
        issues.append(ReadabilityIssue(
            "long_sentence",
            f"{len(long_sents)} 句超过 {MAX_SENTENCE_CHARS} 字，最长 {_cjk_len(long_sents[0])} 字",
        ))

    # 4) 术语密度 + 是否解释过
    unexp = _jargon_unexplained(raw)
    total = max(_cjk_len(raw), 1)
    density = len(unexp) * 100.0 / total
    if unexp and density > MAX_JARGON_PER_100:
        issues.append(ReadabilityIssue(
            "jargon_density",
            f"未解释术语 {len(unexp)} 个（密度 {density:.1f}/100 字）：{', '.join(unexp[:4])}",
        ))

    # 5) 缺少对读者的直接指引（第二人称）
    if not _SECOND_PERSON_RE.search(raw):
        issues.append(ReadabilityIssue(
            "passive_or_vague", "通篇没有直接对读者说话（缺「您/你」）"
        ))

    # 6) 下一步
    if not _NEXT_STEP_RE.search(raw):
        issues.append(ReadabilityIssue(
            "no_next_step", "没告诉患者下一步该做什么（复查/就诊/观察）"
        ))

    # 7) 提到风险但没解释"这意味着什么"
    if re.search(r"恶性|风险|可能|怀疑", raw) and not re.search(
        r"意味着|也就是说|说明|一般|多数|大多数|概率|几率", raw
    ):
        issues.append(ReadabilityIssue(
            "risk_not_explained", "提到了风险但没说明这对患者意味着什么"
        ))

    # 8) 不确定性被隐藏（有不确定词却同时用确定语气）
    if _UNCERTAIN_RE.search(raw) and _CERTAIN_RE.search(raw):
        issues.append(ReadabilityIssue(
            "uncertainty_hidden", "既说「可能」又说「一定/肯定」，口径矛盾"
        ))

    # 9) 恐吓性措辞
    m = _SCARE_RE.search(raw)
    if m:
        issues.append(ReadabilityIssue(
            "scare_language", f"恐吓性措辞：{m.group(0)}"
        ))

    metrics = {
        "chars": float(_cjk_len(raw)),
        "sentences": float(len(sentences)),
        "avg_sentence_chars": round(_cjk_len(raw) / max(len(sentences), 1), 1),
        "jargon_unexplained": float(len(unexp)),
        "jargon_per_100": round(density, 2),
    }
    return ReadabilityReport(
        evaluated=True,
        passed=not issues,
        issues=tuple(issues),
        metrics=metrics,
    )


__all__ = [
    "MAX_SENTENCE_CHARS",
    "ReadabilityIssue",
    "ReadabilityReport",
    "check_readability",
]
