"""多套权威语料并存时的**口径分歧并列**（2026-11-09）。

## 这个模块存在的理由

知识库同时装着多套权威语料，它们在交叉点上给出不同数字。实测（kb_v1.5.0，
逐条核对原文块）：

| 事实 | C-TIRADS 2021 | 甲状腺癌诊疗规范 2018 | 两套都可检索？ |
|---|---|---|---|
| 3 类恶性风险 | ``<2%`` | ``<5%`` | ✅ |
| 5 类恶性风险 | ``>90%`` | ``85%~100%`` | ✅ |
| 4A 类 FNA 阈值 | ``>15 mm`` | ``>1cm（10 mm）`` | ✅ |

（4a/4c 的风险区间在 C-TIRADS 原文里是**表 1 的裸数字**（``2~10``、``50~90``，
% 号只在表头），带 % 的检索式查不到 —— 这是"表格被拍平成一行"导致的，
不是 ``<br>`` 切断。见本模块 `_FACT_PATTERNS` 的注释。）

**两份都不是"错的"**：C-TIRADS 管超声怎么报，诊疗规范管病人全程怎么走。
但在"4A 类 12 mm 要不要穿刺"这种问题上，答案确实不同：

* C-TIRADS：``>15 mm`` 才建议 FNA，12 mm **不满足**（除非多灶/紧邻被膜气管喉返神经 → ``>10 mm`` 可考虑）
* 诊疗规范：``直径>1cm`` 且有恶性征象 → **推荐** US-FNAB

## 本模块的铁律：**不替医生做权威判断**

任何"自动选一个"的实现都是错的。这个模块**只做三件事**：

1. **抽取**：从**已检索到的 chunk 原文**里正则抽出数值事实（:func:`extract_facts`）。
   ⚠️ 数字**只来自 chunk.content**，本文件里**没有一个硬编码的临床数字** ——
   知识库变了，并列块跟着变；知识库没有的，这里也不会凭空长出来。
2. **比对**：同一事实在不同 ``source_set`` 下取值不同 → 判为分歧
   （:func:`detect_conflicts`）。
3. **并列渲染**：把每个来源的取值 + 机构 + 原文引用一起给出，并**明确写
   "请结合本院常规选择"**（:func:`render`）。

不排序、不打分、不说"以哪份为准"。

## 为什么不复用 `app/fusion/conflict.py`

那个模块判的是**多个智能体响应之间**的分级冲突（TR3 vs TR4），服务于融合层；
它看的是 ``AgentResponse.content``，且不知道 "source_set" 这个概念。
本模块判的是**同一次检索内、不同语料之间的数值口径分歧**，服务于提示词组装层。
两者输入/输出/处置都不同（前者触发复核，后者要求并列呈现），合并会让两个
场景互相污染。

## 失败安全

* 抽不出事实 → 返回空列表（**不是**"没有分歧"，而是"没检测到"，
  两个状态在 :class:`ConflictReport` 里分开表达，不合并）；
* 任何异常 → 记 warning、返回空报告，**绝不打断检索引擎或生成**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.knowledge.protocols import KnowledgeChunk
from app.knowledge.source_set import (
    SOURCE_SET_UNKNOWN,
    display_name_of,
    label_of,
    normalize_source_set,
    source_set_of,
)

logger = structlog.get_logger("app.rag.source_conflict")


# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------

#: 参与并列比对的最少语料集数量。**< 2 就不可能有分歧**（同一集内部数字不同
#: 属于"语料自相矛盾"，那是另一类问题，不在本模块职责内）。
MIN_SOURCE_SETS_FOR_CONFLICT = 2

#: 渲染时每段原文引用保留的字符数（太长会把 prompt 撑爆，太短医生无法核对）。
QUOTE_CHARS = 90

#: 判定"这两个取值算不同"时，浮点比较的容差（单位换算后可能差 0.1 mm）。
_VALUE_EPS = 1e-6

#: 事实族：``fact_key`` 前缀 → 人可读标题。**新增事实族必须同时加到这里**，
#: 否则渲染层拿不到标题（单测钉住一致性）。
FACT_TITLES: dict[str, str] = {
    "malignant_risk": "恶性风险",
    "fna_threshold": "FNA（细针穿刺）阈值",
}

#: 类别码 → 显示写法（渲染用；不参与比对）。
_CATEGORY_DISPLAY = {
    "3": "3 类",
    "4a": "4A 类",
    "4b": "4B 类",
    "4c": "4C 类",
    "5": "5 类",
}

#: 波浪号的四种写法（语料里同时存在 ASCII 与全角，混用会导致漏抽）。
_TILDE = r"[~～〜∽]"

#: 「一个风险取值」的形态：``<2%`` / ``<5%`` / ``5%~10%`` / ``10~50%``。
#:
#: 关键在 ``(?:%|(?={_TILDE}))`` 这一句：**必须**要么紧跟 ``%``，要么紧跟波浪号。
#: 不加这个约束，``(?P<lo>\d+)`` 会把「具有 **1** 种恶性征象」里的 ``1`` 当成风险值
#: —— 那就是系统自己在编数字，比不抽取坏得多。
_RISK_VALUE = (
    rf"(?P<lo>[<>＜＞]?\s*\d+(?:\.\d+)?)\s*"
    rf"(?:%|(?={_TILDE}))"
    rf"(?:{_TILDE}\s*(?P<hi>\d+(?:\.\d+)?)\s*%)?"
)

#: 标记剥离：PDF 转 Markdown 时残留的 ``<sup>`` / ``<u>`` / ``<br>``。
#:
#: **必须剥掉再抽**：C-TIRADS 原文里是
#: ``(4)TIRADS4A<sup>类(恶性可能</sup> 2%~10%<sup>)</sup>``，
#: 不剥的话 ``类(恶性可能`` 与 ``2%~10%`` 之间夹着标签，正则匹配不上。
_MARKUP_RE = re.compile(r"</?(?:sup|u|b|i|br|strong|em)\s*/?>", re.IGNORECASE)

#: 渲染 ``literal`` 时要去掉的表格噪声（``**`` 加粗标记与单元格分隔符 ``|``）。
#:
#: 但**只清理展示形态，不清理数据**：``value`` 与比对逻辑仍走未清理的原文。
#: 表格里 ``|**4a**||具有1种恶性征象|5%~10%|`` 直接念给医生看是乱码，
#: 清理后是 ``4a 具有1种恶性征象 5%~10%`` —— 同一行原文，可核对。
_LITERAL_NOISE_RE = re.compile(r"[|*]|</?[^>]{1,12}>")


# ----------------------------------------------------------------------
# 事实模式表（声明式；**所有数字都由 content 提供**）
# ----------------------------------------------------------------------

#: 抽取模式：``(name, fact_key_template, regex)``。
#:
#: 每组正则必须给出 3 个命名组：``cat``（类别）、``lo``（下界）、``hi``（上界）。
#: ``lo``/``hi`` 任一可为 ``None``（开区间，如 ``<2%``）。
#:
#: ## 为什么只有这两种形态（而不是"把所有表都抽出来"）
#:
#: 1. **显式区间 + 恶性可能标记**：C-TIRADS 正文写法
#:    ``TIRADS4A类(恶性可能 2%~10%)``；
#: 2. **征象数描述 + 区间**：诊疗规范表格被拍平后的写法
#:    ``4a  具有 1 种恶性征象 5%~10%``。
#:
#: **刻意不抽"裸表格"**（如 C-TIRADS 表 1 的 ``|3|良性可能| ||1|2~10|4A低度可疑恶性|``）：
#: 那种排列里区间**出现在类别之前**（``2~10`` 是 4A 行的值，却排在 ``4A`` 前），
#: 用"类别后第一个区间"的规则会把它错配给 3 类 —— 那等于**系统自己编了一个数**，
#: 比不检测更坏。抽不到的后果是"这一条不并列"，医生看到的还是原文块本身，
#: 不会看到错的数字。
_FACT_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    # ---- 1. 恶性风险：显式「恶性可能」标记（C-TIRADS 正文） ----
    (
        "risk_explicit",
        "malignant_risk:{cat}",
        re.compile(
            rf"(?P<cat>4A|4B|4C|4a|4b|4c|3|5)\s*类?\s*[（(]?\s*恶性可能\s*[)）]?\s*"
            rf"{_RISK_VALUE}",
        ),
    ),
    # ---- 2. 恶性风险：征象数 / 定性描述后跟区间（诊疗规范表格拍平） ----
    #
    # 两种拍平形态都要吃下（kb_v1.5.0 实测同一个表格的两个 chunk 长得不一样）：
    #   * 纯文本（``norm2018_s01``）：``4a  具有 1 种恶性征象 5%~10%``
    #   * 管道表格（``pdf_..._norm_2018_0007``）：``|**4a**||具有1种恶性征象|5%~10%|``
    # 所以 ``4a`` 与后面的描述之间允许出现 ``|`` ``*`` 空白（``[\s|*]*``）。
    #
    # ⚠️ 「具有 3～4 种恶性征象」里的**征象数本身也可以是区间**（4c 行就是这样）。
    # 只写 ``\d+`` 会让 4c 抽不到 —— 实测漏掉 ``50%~85%`` 这条（C-TIRADS 是
    # ``50%~90%``），即"最难发现的那种漏抽"：另一侧能抽到，于是并列块里只剩
    # 一套来源，系统看起来"没有问题"。
    (
        "risk_signcount",
        "malignant_risk:{cat}",
        re.compile(
            rf"(?P<cat>4a|4b|4c|3|5)[\s|*]*"
            rf"(?:具有\s*\d+(?:\s*[~～〜-]\s*\d+)?\s*种恶性征象|可能良性|恶性)"
            rf"[\s|*]{{0,3}}[^。；;|]{{0,40}}?"
            rf"{_RISK_VALUE}",
        ),
    ),
    # ---- 3. FNA 直径阈值：C-TIRADS「如果结节 > 15 mm … FNA」 ----
    #
    # ⚠️ 比较符必须**落在 lo 组里**（``(?P<lo>[>＞]?\s*\d+...)``）。
    # 首版把 ``[>＞]`` 放在组外，于是 ``> 15 mm`` 被抽成 ``("eq", 15.0)`` ——
    # 一个开区间被当成了"等于"，跨语料比对时会跟真正的 15 mm 阈值判成一致。
    # 这类"归一化改变了语义"的 bug 不报错，只会让并列块静默消失。
    (
        "fna_mm",
        "fna_threshold",
        re.compile(
            r"结节\s*(?P<lo>[>＞<＜]?\s*\d+(?:\.\d+)?)\s*(?P<unit>mm|cm)[^。；;]{0,26}?FNA",
            re.IGNORECASE,
        ),
    ),
    # ---- 4. FNA 直径阈值：诊疗规范「直径>1cm 的甲状腺结节 … 推荐进行 US-FNAB」 ----
    (
        "fna_cm_norm",
        "fna_threshold",
        re.compile(
            r"直径\s*(?P<lo>[>＞<＜]?\s*\d+(?:\.\d+)?)\s*(?P<unit>mm|cm)[^。；;]{0,40}?FNA",
            re.IGNORECASE,
        ),
    ),
)


# ----------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class NumericFact:
    """从一条 chunk 原文里抽出的**一个**数值事实。

    Attributes:
        fact_key: 事实标识（如 ``malignant_risk:4a`` / ``fna_threshold``）。
        value: 归一化后的取值：开区间为 ``("lt"|"gt", x)``，区间为 ``("range", lo, hi)``。
            单位一律归一到 ``mm``（直径）或 ``%``（风险）。
        literal: **原文里怎么写的**（``> 15 mm`` / ``5%~10%``）。渲染时优先用这个，
            因为医生要核对的是原文，不是归一化结果。
        source_set: 该事实来自哪套语料。
        chunk_id: 出处 chunk（可追溯）。
        quote: 原文片段（供医生核对）。
    """

    fact_key: str
    value: tuple[Any, ...]
    literal: str
    source_set: str
    chunk_id: str
    quote: str


@dataclass
class SourceValue:
    """某个来源对某事实给出的取值（同一来源可能给了多次，取第一次并记次数）。"""

    source_set: str
    value: tuple[Any, ...]
    literal: str
    chunk_id: str
    quote: str


@dataclass
class Conflict:
    """一项被判定为**跨语料口径分歧**的事实。"""

    fact_key: str
    #: 每个来源给出的取值（按 source_set 首次出现顺序）。
    values: list[SourceValue] = field(default_factory=list)

    @property
    def title(self) -> str:
        return fact_title_of(self.fact_key)


@dataclass
class ConflictReport:
    """一次检索的语料口径分歧检测结果。

    ## 为什么把 ``detected`` 与 ``conflicts`` 分开

    "没检测到分歧" 与 "没检测" 是两件事：

    * ``detected=False`` → 本次**有多于一套语料、且至少抽出过一个可比事实**，
      只是数值都一致；
    * ``detected=False`` 且 ``source_sets`` 只有 1 个 → **不可能**有跨语料分歧；
    * ``detected=False`` 且 ``facts_extracted == 0`` → **抽不出可比事实**
      （语料是散文，没有结构化数字），这时**不能说"口径一致"**。

    合并这三个状态会让医生以为"系统比对过了、没问题"，那是虚假保证。
    """

    #: 本次证据里出现的 source_set（排序后，``unknown`` 也计入）。
    source_sets: list[str] = field(default_factory=list)
    #: 抽出的可比事实条数（含各来源重复）。
    facts_extracted: int = 0
    #: 判定出的分歧项。
    conflicts: list[Conflict] = field(default_factory=list)
    #: 检测过程中的异常（非空说明本次结论不完整，必须让调用方看到）。
    errors: list[str] = field(default_factory=list)

    @property
    def detected(self) -> bool:
        return bool(self.conflicts)

    @property
    def multi_source(self) -> bool:
        return len(self.source_sets) >= MIN_SOURCE_SETS_FOR_CONFLICT

    @property
    def comparable(self) -> bool:
        """本次是否真的做过有意义的比对（多来源 + 至少一条事实）。"""
        return self.multi_source and self.facts_extracted > 0

    def summary(self) -> str:
        """一行摘要（进 trace / 日志，不进口语化正文）。"""
        if self.errors:
            return f"source_conflict_error: {';'.join(self.errors[:2])}"
        if self.detected:
            return (
                f"source_conflict: {len(self.conflicts)} 项跨语料分歧"
                f"（来源 {','.join(self.source_sets)}）"
            )
        if not self.multi_source:
            return f"source_conflict: 单一来源（{','.join(self.source_sets) or '无'}），无跨语料分歧"
        if not self.comparable:
            return (
                f"source_conflict: 多来源但无可比数值事实"
                f"（来源 {','.join(self.source_sets)}）—— 未比对，不等于一致"
            )
        return f"source_conflict: 已比对 {self.facts_extracted} 条事实，数值一致"


# ----------------------------------------------------------------------
# 抽取
# ----------------------------------------------------------------------


def _strip_markup(text: str) -> str:
    """剥掉 PDF 转 Markdown 的残留标签，并把连续空白压成单空格。"""
    if not text:
        return ""
    out = _MARKUP_RE.sub(" ", text)
    return re.sub(r"[ \t\u3000]+", " ", out)


def _normalize_category(raw: str) -> str:
    return str(raw or "").strip().lower()


def _normalize_value(
    lo: str | None, hi: str | None, unit: str | None = None
) -> tuple[Any, ...] | None:
    """把抽到的两个边界归一成可比较的元组（纯函数）。

    * ``>15`` → ``("gt", 15.0)``
    * ``<2`` → ``("lt", 2.0)``
    * ``5``~``10`` → ``("range", 5.0, 10.0)``

    单位：``cm`` → ``mm``（×10）。**这是对原文数字的换算，不是编造**；
    渲染时用 ``literal``（原文写法），所以医生看到的是 ">1cm" 而不是 ">10 mm"。
    """
    hi_val = None
    if hi is not None:
        try:
            hi_val = float(hi)
        except (TypeError, ValueError):
            return None
    if lo is None:
        return None
    lo_raw = str(lo).strip()
    opener = ""
    if lo_raw[:1] in "<＞>＜":
        opener = lo_raw[0]
        lo_raw = lo_raw[1:].strip()
        # 全角转半角，保证比较口径统一
        opener = {"＜": "<", "＞": ">"}.get(opener, opener)
    try:
        lo_val = float(lo_raw)
    except (TypeError, ValueError):
        return None
    if unit and unit.lower() == "cm":
        lo_val *= 10.0
        if hi_val is not None:
            hi_val *= 10.0
    if hi_val is not None:
        return ("range", lo_val, hi_val)
    if opener == "<":
        return ("lt", lo_val)
    if opener == ">":
        return ("gt", lo_val)
    return ("eq", lo_val)


def _quote_around(text: str, start: int, end: int, width: int = QUOTE_CHARS) -> str:
    """截取命中位置附近的一段原文（保证片段自己能读通，不截半句）。"""
    pad = max(0, (width - (end - start)) // 2)
    left = max(0, start - pad)
    right = min(len(text), end + pad)
    snippet = text[left:right].strip()
    snippet = re.sub(r"\s+", " ", snippet)
    if left > 0:
        snippet = "…" + snippet
    if right < len(text):
        snippet = snippet + "…"
    return snippet


def _clean_literal(raw: str) -> str:
    """把命中的原文片段清理成**医生能读**的形态（只清表格噪声，不改数字）。

    ``4a**||具有1种恶性征象|5%~10%`` → ``4a 具有1种恶性征象 5%~10%``

    **不删任何数字、不补任何数字**：只把 markdown 表格的 ``|`` / ``**`` 与
    残留标签换成空格，再压空白。所以清理后的字符串仍是原文的子序列，
    医生能逐字核对。
    """
    out = _LITERAL_NOISE_RE.sub(" ", raw or "")
    return re.sub(r"\s+", " ", out).strip()


def extract_facts(chunk: KnowledgeChunk, source_set: str | None = None) -> list[NumericFact]:
    """从**一条** chunk 的 ``content`` 抽取数值事实。纯函数。

    Args:
        chunk: 已检索到的知识块。数字**全部**来自 ``chunk.content``。
        source_set: 归属语料集；``None`` 时按 ``source_doc_id`` 现算
            （与检索层同一把尺子，见 ``app.knowledge.source_set.source_set_of``）。

    Returns:
        抽到的事实列表（可能为空 —— 散文段落里没有结构化数字）。

    **不抛异常**：单条 chunk 抽失败只返回已抽到的部分。
    """
    text = _strip_markup(chunk.content or "")
    if not text:
        return []
    ss = normalize_source_set(source_set) or source_set_of(
        source_doc_id=chunk.source_doc_id, metadata=chunk.metadata
    )
    out: list[NumericFact] = []
    for _name, key_template, pattern in _FACT_PATTERNS:
        try:
            for m in pattern.finditer(text):
                groups = m.groupdict()
                value = _normalize_value(
                    groups.get("lo"), groups.get("hi"), groups.get("unit")
                )
                if value is None:
                    continue
                cat = groups.get("cat")
                fact_key = key_template.format(cat=_normalize_category(cat)) if cat else key_template
                out.append(
                    NumericFact(
                        fact_key=fact_key,
                        value=value,
                        literal=_clean_literal(m.group(0)),
                        source_set=ss,
                        chunk_id=chunk.chunk_id,
                        quote=_quote_around(text, m.start(), m.end()),
                    )
                )
        except Exception as exc:  # silent-ok: 单条 chunk 抽失败不打断整批
            logger.warning(
                "source_conflict_extract_failed",
                chunk_id=getattr(chunk, "chunk_id", ""),
                pattern=_name,
                error=str(exc)[:160],
                error_type=type(exc).__name__,
            )
    return out


def _values_differ(a: tuple[Any, ...], b: tuple[Any, ...]) -> bool:
    """两个归一化取值是否**实质不同**（纯函数）。

    形态不同但数值等价的算相同吗？**算不同**。例如 C-TIRADS 的 ``>90%``
    与诊疗规范的 ``85%~100%``：区间并不等价，硬说"一致"等于替医生做了判断。
    只有**形态与数值都相同**才判一致。
    """
    if a == b:
        return False
    if len(a) != len(b):
        return True
    if a[0] != b[0]:
        return True
    try:
        return any(
            abs(float(x) - float(y)) > _VALUE_EPS
            for x, y in zip(a[1:], b[1:], strict=False)
        )
    except (TypeError, ValueError):
        return True


def detect_conflicts(
    chunks: list[KnowledgeChunk],
    *,
    source_sets: list[str] | None = None,
) -> ConflictReport:
    """检测本次检索证据里的**跨语料口径分歧**。纯函数（无 IO、无副作用）。

    Args:
        chunks: 本次检索到的知识块（顺序无关）。
        source_sets: 可选，显式指定归属（测试用）。``None`` = 每条 chunk 现算。

    Returns:
        :class:`ConflictReport`。**永不为 ``None``**，也永不抛异常。

    ## 判定规则（三条，都要满足才算分歧）

    1. 同一 ``fact_key``（如 ``malignant_risk:4a`` / ``fna_threshold``）；
    2. 来自**不同** ``source_set``；
    3. 归一化取值**不相等**（:func:`_values_differ`）。

    同一来源内部前后矛盾（同一 fact_key 两个值）**不算跨语料分歧** ——
    那是"语料自相矛盾"，处置是修语料，不是并列。这里只记第一个取值，
    并在 ``errors`` 之外不额外报（避免把两类问题混成一个）。
    """
    report = ConflictReport()
    try:
        facts: list[NumericFact] = []
        seen_sets: list[str] = []
        for idx, chunk in enumerate(chunks or []):
            explicit = source_sets[idx] if source_sets and idx < len(source_sets) else None
            for fact in extract_facts(chunk, explicit):
                facts.append(fact)
                if fact.source_set not in seen_sets:
                    seen_sets.append(fact.source_set)
            if explicit:
                ss = normalize_source_set(explicit) or SOURCE_SET_UNKNOWN
            else:
                ss = source_set_of(
                    source_doc_id=chunk.source_doc_id, metadata=chunk.metadata
                )
            if ss not in seen_sets:
                seen_sets.append(ss)

        report.source_sets = seen_sets
        report.facts_extracted = len(facts)

        # 同一 fact_key 内，按 source_set 折叠（每来源只留首次取值）
        by_key: dict[str, list[SourceValue]] = {}
        for fact in facts:
            bucket = by_key.setdefault(fact.fact_key, [])
            first = next((v for v in bucket if v.source_set == fact.source_set), None)
            if first is None:
                bucket.append(
                    SourceValue(
                        source_set=fact.source_set,
                        value=fact.value,
                        literal=fact.literal,
                        chunk_id=fact.chunk_id,
                        quote=fact.quote,
                    )
                )
            elif _values_differ(first.value, fact.value):
                # 同一来源给了两个不同值：不是跨语料分歧，但**必须留痕**，
                # 否则"检测过了、没问题"是假的。
                report.errors.append(
                    f"intra_source_inconsistency:{fact.fact_key}:{fact.source_set}"
                )

        for fact_key, values in by_key.items():
            if len(values) < MIN_SOURCE_SETS_FOR_CONFLICT:
                continue
            base = values[0].value
            if any(_values_differ(base, v.value) for v in values[1:]):
                report.conflicts.append(Conflict(fact_key=fact_key, values=values))

        # 稳定顺序：条目少的排前面（单点阈值比多类别风险更该先说），再按 key
        report.conflicts.sort(key=lambda c: (len(c.fact_key), c.fact_key))
        return report
    except Exception as exc:  # silent-ok: 检测失败绝不能打断检索/生成
        logger.warning(
            "source_conflict_detect_failed",
            error=str(exc)[:200],
            error_type=type(exc).__name__,
            chunk_count=len(chunks or []),
        )
        report.errors.append(f"detect_failed:{type(exc).__name__}")
        return report


# ----------------------------------------------------------------------
# 渲染
# ----------------------------------------------------------------------


def fact_title_of(fact_key: str) -> str:
    """``fact_key`` → 人可读标题（纯函数）。"""
    head, _, cat = str(fact_key or "").partition(":")
    base = FACT_TITLES.get(head, head)
    if cat:
        return f"{base}：{_CATEGORY_DISPLAY.get(cat, cat)}"
    return base


def render(report: ConflictReport, *, heading: str = "# 语料口径分歧") -> str:
    """把分歧报告渲染成**可直接塞进 prompt** 的段落（纯函数）。

    Returns:
        空串表示**不该插入任何东西**（没有分歧，或本次根本没比对成）。
        调用方据此决定是否改动 prompt —— 没有分歧时 prompt 必须**逐字不变**
        （否则每次生成都多一段噪声，白白吃 token 且会分散模型注意力）。

    渲染格式（医生要能一眼看出"这是两份权威在打架，不是系统在猜"）::

        # 语料口径分歧（同一问题，多套权威给出不同数值）

        本次证据来自 2 套语料，对 1 项事实给出不同口径。
        **不要替医生合并成一个数**：必须在结论里并列呈现全部来源并注明出处。

        ## 分歧 1：FNA（细针穿刺）阈值
        - 依据《C-TIRADS 2021》(中华医学会超声医学分会)：> 15 mm
          原文：「…如果结节 > 15 mm, 建议超声引导下 FNA…」
        - 依据《甲状腺癌诊疗规范 2018》(国家卫生健康委员会)：> 1 cm
          原文：「…直径>1cm 的甲状腺结节, US 有恶性征象者, 推荐进行 US-FNAB…」

        两份权威给出不同数值。请结合本院常规选择，并在结论中写明依据的是哪一份。
    """
    if not report or not report.detected:
        return ""
    lines: list[str] = [heading + "（同一问题，多套权威给出不同数值）", ""]
    lines.append(
        f"本次证据来自 {len(report.source_sets)} 套语料，对 {len(report.conflicts)} 项事实"
        f"给出不同口径。"
    )
    lines.append(
        "**不要替医生合并成一个数**：必须在结论里并列呈现全部来源并注明出处；"
        "不确定时写「两份权威不一致，请结合本院常规」。"
    )
    for i, conflict in enumerate(report.conflicts, start=1):
        lines.append("")
        lines.append(f"## 分歧 {i}：{conflict.title}")
        for v in conflict.values:
            name, org = label_of(v.source_set)
            lines.append(f"- 依据《{name}》({org})：{v.literal}")
            if v.quote:
                lines.append(f"  原文：「{v.quote}」")
    lines.append("")
    lines.append(
        "两份权威给出不同数值。请结合本院常规选择，并在结论中写明依据的是哪一份。"
    )
    return "\n".join(lines)


def trace_lines(report: ConflictReport) -> list[str]:
    """给 ``reasoning_trace`` 用的若干行（可追溯、不占正文）。"""
    lines = [report.summary()]
    for conflict in report.conflicts:
        detail = " / ".join(f"{v.source_set}={v.literal}" for v in conflict.values)
        lines.append(f"source_conflict_detail: {conflict.fact_key} -> {detail}")
    return lines


def summary_of(report: ConflictReport | None) -> dict[str, Any]:
    """结构化摘要（写进 findings / API 响应，前端可据此展示"存在口径分歧"徽标）。"""
    if report is None:
        return {"detected": False, "comparable": False, "conflicts": [], "source_sets": []}
    return {
        "detected": report.detected,
        "comparable": report.comparable,
        "multi_source": report.multi_source,
        "source_sets": list(report.source_sets),
        "facts_extracted": report.facts_extracted,
        "conflicts": [
            {
                "fact_key": c.fact_key,
                "title": c.title,
                "values": [
                    {
                        "source_set": v.source_set,
                        "label": display_name_of(v.source_set),
                        "organization": label_of(v.source_set)[1],
                        "literal": v.literal,
                        "chunk_id": v.chunk_id,
                    }
                    for v in c.values
                ],
            }
            for c in report.conflicts
        ],
        "errors": list(report.errors),
    }


__all__ = [
    "FACT_TITLES",
    "MIN_SOURCE_SETS_FOR_CONFLICT",
    "Conflict",
    "ConflictReport",
    "NumericFact",
    "SourceValue",
    "detect_conflicts",
    "extract_facts",
    "fact_title_of",
    "render",
    "summary_of",
    "trace_lines",
]
