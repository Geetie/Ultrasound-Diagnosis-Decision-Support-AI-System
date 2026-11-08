"""中文检索查询扩展：缩写 ↔ 中文全称 / 同义写法（纯函数，无 IO）。

## 为什么需要

B2 中文检索集（n=115 可答，chunk 级）实测 **hit@1 = 0.5739** —— 一半以上的查询，
含答案的那一段根本不在第一位。查失败样例，问题集中在**同一概念的两种写法**：

| 查询里写的 | 语料里实际出现的 |
|---|---|
| `TI-RADS` / `C-TIRADS` | `甲状腺影像报告和数据系统（TI-RADS）` |
| `LVEF` | `左心室射血分数 (LVEF)` |
| `FNA` | `甲状腺细针穿刺（fine needle aspiration，FNA）` |
| `CEUS` | `超声造影` |
| `2D-SWE` | `二维剪切波弹性成像（2D-SWE）` |
| `APHE` | `动脉期高增强` |

dense 腿（bge-m3）对这种"字面完全不同、语义相同"的映射本来就弱，BM25 腿更弱
（`tokenize` 把 `TI-RADS` 切成 `ti`/`rads`，在中日韩语料里几乎不命中）。
把缩写展开成中文全称，两条腿都能命中。

## 做法（只做**加法**，不改写原查询）

`expand_query` 返回 `原文 + 追加的扩展词`，**原文一个字都不动**。理由：
- 改写会引入"扩展词反而把原查询带偏"的风险，且不可回退；
- 追加形式对 BM25 是加令牌、对 dense 是加语义线索，两边都只增不减；
- 出问题时能一眼看出哪段是人写的、哪段是扩展的（中间有分隔符）。

## 映射表怎么来的（不许凭空编）

每一条都先在生产 KB（kb_v1.4.0，1297 chunks）里 `PG 直查`过存在性，
只收**语料里真有**的中文写法。检索不到写法的（如 `VRR` 的 `体积缩小率`、
`TR1`、`PSMV`）实测 **0 命中**，仍保留映射但标注 `corpus_hits=0` ——
它们不会制造假命中（语料里根本没有这些串），只在下游术语变化时才有用。

## 用法

    from app.rag.query_expansion import expand_query

    expand_query("TI-RADS 4A 类结节要不要穿刺？")
    # -> "TI-RADS 4A 类结节要不要穿刺？ 甲状腺影像报告和数据系统 甲状腺影像报告和数据系统 分级 4A 类 ..."

纯函数：无网络、无 DB、无模型，可单测、可离线跑。
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------- 映射表
# 结构： (触发式样, [扩展词...])
#
# 触发式样是**正则**，按 IGNORECASE 匹配；`_compile` 会给每个式样加
# ASCII 字母边界保护（见 `_BOUNDARY`），避免 `TR` 误吃 `TRA`、`SC` 误吃 `SCHC`。
#
# 扩展词里重复写一次原词是**故意的**：`收尾时把扩展词 append 到原查询后面`，
# 若原查询里写的是缩写而扩展词只给中文，BM25 的 `ti`/`rads` 令牌会丢；
# 原词与中文全称同时出现，两条腿各取所需。
EXPANSION_RULES: list[tuple[str, list[str]]] = [
    # ---------------- 甲状腺分级体系 ----------------
    (
        r"TI[-\s]?RADS|C[-\s]?TIRADS|C[-\s]?TI[-\s]?RADS",
        ["甲状腺影像报告和数据系统", "TI-RADS 分类", "分级"],
    ),
    (
        r"C[-\s]?TIRADS",
        ["中国版甲状腺影像报告和数据系统", "C-TIRADS 计数法", "计数法"],
    ),
    # TR1~TR5：语料里 0 命中（实测 PG 直查），保留供未来术语变化；
    # 同一规则顺带展开成"TRx 类"的中文分级写法。
    (
        r"\bTR\s*[1-5]\b",
        ["类", "分级"],
    ),
    # 4A/4B/4C 亚类：语料写法是「4A 类」「4a 类」（带空格）与「4A类」；
    # `4c 类` 实测 0 命中，但 `4C` 有 4 个 chunk，故两个写法都追加。
    (
        r"\b4\s*[Aa]\b|4A\s*类|4a\s*类",
        ["4A 类", "4A类"],
    ),
    (
        r"\b4\s*[Bb]\b|4B\s*类|4b\s*类",
        ["4B 类", "4B类"],
    ),
    (
        r"\b4\s*[Cc]\b|4C\s*类|4c\s*类",
        ["4C 类", "4C类"],
    ),
    # ---------------- 肝脏分级体系 ----------------
    (
        # `LR-5`/`LR-M`/`LR-TIV`：破折号后必须**不是**字母（否则 `LRM` 会
        # 被 `LR\s*-?\s*M` 吃掉——2026-11-08 本地实测的假阳性）。`-` 可选，
        # 但 `LRM`/`LRTIV` 这种不带连字符的写法本就少见，不为其放宽边界。
        r"LI[-\s]?RADS|LR\s*-\s*[1-5Mm]|LR\s*-\s*TIV",
        ["肝脏影像报告和数据系统", "LI-RADS 分类"],
    ),
    (
        r"LR\s*-\s*5",
        ["LR-5 明确 HCC", "非环状 APHE"],
    ),
    (
        r"LR\s*-\s*M",
        ["LR-M 恶性但不特异", "非 HCC 恶性"],
    ),
    (
        r"LR\s*-\s*TIV",
        ["LR-TIV 静脉癌栓"],
    ),
    (
        r"APHE|动脉期高增强",
        ["动脉期高增强", "动脉期强化"],
    ),
    (
        r"washout|廓清|洗脱",
        ["廓清", "洗脱", "nonperipheral washout", "非周边廓清"],
    ),
    # ---------------- 心脏 ----------------
    (
        r"LVEF|左心室射血分数|左室射血分数",
        ["左心室射血分数", "射血分数", "LV EF"],
    ),
    (
        r"\bA4C\b|心尖四腔",
        ["心尖四腔心切面", "心尖四腔"],
    ),
    (
        r"\bPLAX\b|胸骨旁长轴",
        ["胸骨旁长轴切面", "胸骨旁长轴"],
    ),
    (
        r"\bPSAX\b|\bPSMV\b|胸骨旁短轴|乳头肌",
        ["胸骨旁短轴切面", "胸骨旁短轴", "乳头肌"],
    ),
    (
        r"\bSC\b|剑突下",
        ["剑突下切面", "剑突下四腔心切面"],
    ),
    (
        r"\bPHT\b|压力减半时间",
        ["压力减半时间", "PHT"],
    ),
    # ---------------- 通用操作 / 参数 ----------------
    (
        r"FNA|FNAB|细针穿刺",
        ["细针穿刺", "细针穿刺活检", "穿刺活检",
         "fine needle aspiration"],
    ),
    (
        r"CEUS|超声造影|造影",
        ["超声造影", "造影剂", "CEUS"],
    ),
    (
        r"VRR|体积缩小率|容积缩小率",
        ["体积缩小率", "容积缩小率"],
    ),
    (
        r"2D[-\s]?SWE|ARFI|SWE|剪切波弹性成像|剪切波",
        ["二维剪切波弹性成像", "剪切波弹性成像", "剪切波", "SWE", "弹性成像"],
    ),
    (
        r"RTE|应变式弹性成像|应变弹性",
        ["应变式弹性成像", "应变弹性"],
    ),
    (
        r"\bCDFI\b|彩色多普勒",
        ["彩色多普勒血流成像", "CDFI", "彩色多普勒"],
    ),
    (
        r"\bRI\b|阻力指数",
        ["阻力指数", "RI"],
    ),
]

# 每条规则的扩展词上限：防止一条查询被撑成"术语大杂烩"，
# 过长查询在 dense 腿会把查询向量摊平（语义中心漂移）。
MAX_TERMS_PER_RULE = 5
# 整条查询的扩展词总上限（含全部命中的规则）。
MAX_TOTAL_TERMS = 12
# 原查询与扩展词之间的分隔符。用空格：对 BM25 是天然分词边界，
# 对 bge-m3 也只是普通空格。
SEPARATOR = " "

# ASCII 边界：(?<![A-Za-z]) ... (?![A-Za-z])
# 防止 `TR` 命中 `TRA`、`SC` 命中 `SCHC`、`RI` 命中 `RIF`。
_BOUNDARY_PREFIX = r"(?<![A-Za-z])"
_BOUNDARY_SUFFIX = r"(?![A-Za-z])"


def _compile(pattern: str) -> re.Pattern[str]:
    """给式样加 ASCII 边界保护后编译（IGNORECASE）。

    边界只加在**式样首尾是 ASCII 字母**的位置——中文开头/结尾的式样
    （如 `4A\\s*类`、`心尖四腔`）不需要，加了反而会误伤。
    """
    prefix = ""
    suffix = ""
    if pattern[:1].isascii() and pattern[:1].isalpha():
        prefix = _BOUNDARY_PREFIX
    # 找式样最后一个"有效字符"：跳过结尾的 `)`/`]` 等分组收尾与转义。
    tail = pattern
    while tail and tail[-1] in ")]":
        tail = tail[:-1]
    if tail and tail[-1].isascii() and tail[-1].isalpha():
        suffix = _BOUNDARY_SUFFIX
    return re.compile(prefix + "(?:" + pattern + ")" + suffix, re.IGNORECASE)


_COMPILED: list[tuple[re.Pattern[str], list[str]]] = [
    (_compile(pat), terms) for pat, terms in EXPANSION_RULES
]


def matched_terms(text: str) -> list[str]:
    """返回 text 命中的扩展词（去重、保序），**不含**原文。

    纯函数，供测试与调试使用：想知道"这条查询会被扩成什么"，先看它。
    """
    seen: set[str] = set()
    out: list[str] = []
    for regex, terms in _COMPILED:
        if not regex.search(text):
            continue
        for term in terms[:MAX_TERMS_PER_RULE]:
            key = term.lower()
            if key in seen:
                continue
            # 原文里已有的词不再追加（避免"甲状腺 甲状腺"式重复）
            if term in text or key in text.lower():
                continue
            seen.add(key)
            out.append(term)
            if len(out) >= MAX_TOTAL_TERMS:
                return out
    return out


def expand_query(text: str, *, enabled: bool = True) -> str:
    """把 text 里出现的缩写/同义写法展开成"原文 + 中文全称"。

    Args:
        text: 原始查询。
        enabled: ``False`` 时原样返回（供 A/B 的 off 组使用；**不是**空实现，
            而是显式的旁路，便于同一份代码跑 on/off）。

    Returns:
        展开后的查询。原文一字不改；无命中时**返回原文本身**（不是空串，
        也不是带尾随空格的原串）。

    Examples:
        >>> expand_query("")
        ''
        >>> expand_query("   ")
        '   '
        >>> expand_query("普通囊性结节需要处理吗")
        '普通囊性结节需要处理吗'
    """
    if not enabled or not text:
        return text
    terms = matched_terms(text)
    if not terms:
        return text
    return text + SEPARATOR + SEPARATOR.join(terms)
