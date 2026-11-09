"""从长临床描述里抽取检索意图（2026-11-09）。

## 为什么需要它

实测发现生产的 `user_query` 是**整段临床描述**（60-120 字），而 B2 测试集是
**短问句**（P50 26 字）。两者形态差很多：

```
生产：'甲状腺右叶实性低回声结节，大小12mm，边界清，形态规则，纵横比<1，
      内部未见明显钙化，CDFI示周边少量血流。TI-RADS 4A类。是否需要穿刺活检？'
B2：  '2022 版指南 4c 类的恶性风险区间是多少？'
```

**长描述里大部分词是影像特征**（「实性」「低回声」「边界清」），
这些词在知识库里到处都有 → 检索被高频词主导，
真正的意图（「4A 类要不要穿刺」）被稀释。

实测：4 条真实描述去检索，**top-1 全部未命中该问题的答案**。

## 与「查询扩展」的区别（关键）

阶段2 试过的是「短 → 长」（加同义词），实测负增益（p=0.2266 判不动）。
**本模块方向相反：长 → 短（去噪）**，这是没试过的方向。

## 设计：纯规则，不调 LLM

理由：
1. 检索是**热路径**，加一次 LLM 调用会让 P99 翻倍
2. 临床描述的结构高度可预测：`影像特征 + 分级码 + 问句`
3. 规则可解释，出问题时能一眼看出抽错了什么

抽三样东西：
1. **分级码**（C-TIRADS 4A / TR4 / LI-RADS LR-5 / LVEF 38%）
2. **问句部分**（含「要不要」「怎么」「是否」等的句子）
3. **关键测量**（结节大小、阈值相关的数值）
"""
from __future__ import annotations

import re

#: 分级码模式（三类器官）
_GRADE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"C-?TIRADS\s*[1-6]\s*[A-Ca-c]?\s*类?", re.IGNORECASE),
    re.compile(r"TI-?RADS\s*[1-6]\s*[A-Ca-c]?", re.IGNORECASE),
    re.compile(r"\bTR\s?[1-5]\b", re.IGNORECASE),
    re.compile(r"LI-?RADS\s*(?:LR-?)?\s*[1-5M]\b", re.IGNORECASE),
    re.compile(r"LR-?[1-5M]\b", re.IGNORECASE),
    re.compile(r"LVEF\s*[=:：]?\s*\d{1,3}\s*%?", re.IGNORECASE),
    re.compile(r"EF\s*[≤≥<>=]+\s*\d{1,3}\s*%", re.IGNORECASE),
)

#: 问句标记 —— 出现这些说明该句是「医生想问的事」，要保留
_ASK_MARKERS = (
    "要不要", "是否需要", "是否", "需不需要", "用不用",
    "怎么", "如何", "为什么", "什么", "多少", "几",
    "是否可", "能否", "可以吗", "怎么办", "下一步",
    "？", "?", "请", "建议", "推荐",
)

#: 影像描述特征词 —— 这些是「噪声」，在检索时会主导匹配
_IMAGING_NOISE = (
    "实性", "囊性", "囊实性", "低回声", "高回声", "无回声", "等回声", "极低回声",
    "边界清", "边界欠清", "边界模糊", "形态规则", "形态不规则", "纵横比",
    "微钙化", "粗钙化", "点状强回声", "后方回声", "声晕", "声影",
    "CDFI", "血流信号", "周边少量血流", "内见", "另见", "可见",
    "甲状腺右叶", "甲状腺左叶", "肝右叶", "肝左叶", "左室", "右室",
)

#: 测量值（结节大小/内径等）—— 保留，因为「多大要穿」这类问题需要它
_MEASURE_RE = re.compile(
    r"(?:大小|直径|内径|厚度|容积|长径|短径)?\s*"
    r"\d+(?:\.\d+)?\s*(?:mm|cm|ml|%|kPa|m/s)",
    re.IGNORECASE,
)

#: 句子切分（**保留终止符**：`？！` 是"这是问句"的最强信号，
#: 不能当分隔符丢掉 —— 实测丢掉的后果是含问号的句子被判成陈述句，
#: 于是 `LI-RADS 分类？` 没被识别为问句，整条抽取退化为原样返回）
_SENT_SPLIT = re.compile(r"(?<=[。；;！!？?])")


def _sentences(text: str) -> list[str]:
    """切句并**保留终止符**。

    实测教训（2026-11-09）：原来用 `[。；！？]+` 做 split，终止符被吃掉，
    于是 `_ASK_MARKERS` 里的 `？`/`?` 永远匹配不到 —— 含问号的句子
    全被判成陈述句。改用零宽断言在终止符**之后**切，终止符留在句尾。
    """
    out: list[str] = []
    for seg in _SENT_SPLIT.split(text or ""):
        seg = seg.strip().rstrip("，,").strip()
        if seg:
            out.append(seg)
    return out


def extract_intent(text: str) -> str:
    """从长临床描述里抽出检索意图。**纯函数，永不抛异常。**

    Returns:
        检索用 query。抽不出意图时**原样返回输入**（不猜、不丢）。

    ## 三条抽取规则

    1. **有问句 → 用问句 + 分级码 + 关键测量**
       `'…边界清…TI-RADS 4A类。是否需要穿刺活检'`
       → `'甲状腺右叶实性低回声结节12mm TI-RADS 4A类 是否需要穿刺活检'`

    2. **无问句但有分级码 → 用分级码 + 器官 + 关键测量**
       这种输入通常是「给个分级，看指南怎么说」

    3. **都没有 → 原样返回**
       不擅自改写，避免把有信息的输入改坏
    """
    raw = (text or "").strip()
    if not raw:
        return raw

    sents = _sentences(raw)

    # ---- 1) 分级码（保留原样，它是检索的强锚点）
    # 分级码去重：`C-TIRADS 4C类` 与 `TIRADS 4C` 是同一个码，
    # 只保留**信息更全**的那个（带 C- 前缀或带「类」字）。
    # 实测不去重会得到 `C-TIRADS 4C类 TIRADS 4C` —— 重复占位、稀释意图。
    _raw_grades: list[str] = []
    for pat in _GRADE_PATTERNS:
        for m in pat.finditer(raw):
            g = m.group(0).strip()
            if g:
                _raw_grades.append(g)

    def _grade_key(g: str) -> str:
        """归一化用于判重：去掉 C-/TIRADS/类/空格，只留核心数字+字母。"""
        k = re.sub(r"(?i)c-?tirads|tirads|li-?rads|\s|[类]", "", g)
        return k.upper()

    grades: list[str] = []
    seen_keys: set[str] = set()
    # 长的优先（`C-TIRADS 4C类` 比 `TIRADS 4C` 信息多）
    for g in sorted(_raw_grades, key=len, reverse=True):
        k = _grade_key(g)
        if k and k not in seen_keys:
            seen_keys.add(k)
            grades.append(g)
    grades.reverse()   # 恢复原文顺序（sort 打乱了）

    # ---- 2) 问句句（按标记识别）
    ask_parts = [s for s in sents if any(mk in s for mk in _ASK_MARKERS)]

    # ---- 3) 关键测量
    measures = _MEASURE_RE.findall(raw)
    # 去重：按「数值+单位」判重（`内径 52mm` 与 `52mm` 是同一个测量）。
    # 实测不去重会得到 `内径 52mm 38% 厚度 12mm 舒张末期内径 52mm…` —— 越抽越长。
    # 最多留 3 个（太多会稀释意图）。
    _num_re = re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|ml|%|kPa|m/s)", re.IGNORECASE)

    seen: set[str] = set()
    uniq_measures: list[str] = []
    for m in measures:
        k = m.strip()
        mk = _num_re.search(k)
        key = mk.group(0).replace(" ", "").lower() if mk else k.lower()
        if key and key not in seen:
            seen.add(key)
            # 只留「数值+单位」，去掉前缀（`内径 ` / `厚度 `）——
            # 前缀是描述，不是检索锚点
            uniq_measures.append(mk.group(0).strip() if mk else k)
        if len(uniq_measures) >= 3:
            break

    # ---- 4) 组装
    def _strip_noise(s: str) -> str:
        """问句句里如果混了影像描述，去掉明显噪声词（不动语义）。"""
        out = s
        for noise in _IMAGING_NOISE:
            out = out.replace(noise, " ")
        return re.sub(r"\s{2,}", " ", out).strip(" ,，、;；")

    if ask_parts:
        # 情形 1：有问句 → 用「关键测量 + 分级 + 纯问句」
        # **先从问句里剥掉已保留的测量值与分级码**，否则会重复：
        #   实测 `大小12mm TI-RADS 4A 是否需要穿刺活检` 里的
        #   「穿刺活检」句本身不含重复，但别的输入会。
        core_ask = " ".join(_strip_noise(s) for s in ask_parts)
        for m in uniq_measures:
            core_ask = core_ask.replace(m, " ")
        for g in grades:
            core_ask = core_ask.replace(g, " ")
            core_ask = re.sub(re.escape(_grade_key(g)), " ", core_ask, flags=re.IGNORECASE)
        core_ask = re.sub(r"\s{2,}", " ", core_ask).strip(" ,，、;；")
        pieces = []
        if uniq_measures:
            pieces.append(" ".join(uniq_measures))
        if grades:
            pieces.append(" ".join(grades))
        if core_ask:
            pieces.append(core_ask)
        out = " ".join(p for p in pieces if p).strip()
        # 兜底：若去噪后太短（<8 字），退回「原始首句 + 分级」
        if len(out) < 8:
            return raw
        return _guard_no_longer(raw, out)

    if grades:
        # 情形 2：有分级无问句 → 分级 + 测量 + **末句**（结论/分级判断通常在最末）
        #
        # 实测修正：原来取 `sents[0][:40]`（首句）—— 首句几乎总是影像描述，
        # 而且截断后常与分级码/测量值重复，导致守卫判"变长了"退回原文。
        # 改用**末句**：临床描述的最后一句往往是判断或结论（如"收缩功能中度降低"）。
        pieces = sorted(grades, key=len, reverse=True)[:2]
        pieces.reverse()
        if uniq_measures:
            pieces.extend(uniq_measures)
        if sents:
            tail = _strip_noise(sents[-1])
            # 末句若与已保留内容高度重复则不加
            if tail and not any(tail in p or p in tail for p in pieces):
                # 只留末句的「判断词」部分，去掉纯测量
                tail_short = re.sub(r"\d+(?:\.\d+)?\s*(?:mm|cm|%|ml|kPa|m/s)", " ", tail)
                tail_short = re.sub(r"\s{2,}", " ", tail_short).strip(" ,，、;；")
                if tail_short and len(tail_short) >= 3:
                    pieces.append(tail_short[:30])
        out2 = " ".join(p for p in pieces if p).strip()
        if len(out2) >= 8:
            return _guard_no_longer(raw, out2)
        return raw

    # 情形 3：都没有 → 原样
    return raw


def _guard_no_longer(raw: str, out: str) -> str:
    """抽取结果**比原文更长**说明规则失效了，退回原文。

    实测反例：`左室舒张末期内径 52mm…E/e 16。收缩功能中度降低。`
    抽出 `LVEF 38% 内径 52mm 38% 厚度 12mm 舒张末期内径 52mm，LVEF 38%…`
    —— 比原文还长，纯粹是重复堆叠。这种宁可不用抽取。
    """
    return out if len(out) < len(raw) else raw


__all__ = ["extract_intent"]
