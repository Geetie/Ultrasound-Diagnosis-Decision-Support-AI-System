"""模型输出守卫：拦截「复述提示词示例」这类脏输出（A.70 / R17）。

**为什么需要**：A.70 真机核查发现，评测集里 **98.7%（155/169）的"结论"其实是提示词示例的照抄** ——
`app/agents/prompts/{thyroid,cardiac,liver}.md` 的「输出」段放了完整示例答案，小模型（1.5B/7B）
在长中文 prompt 下把示例当成要复述的内容，于是输出里出现：

    证据: chunk_a1b2, chunk_c3d4 … 患者PATIENT_001: 该结节为良性
    # 输出 分级: TR2 …（同一段重复 4 次，每次分级不同）

`chunk_a1b2` / `chunk_c3d4` / `PATIENT_001` 都是**模板占位符**（真实证据 ID 形如
`kb_v1.3.0:pdf_..._0003#0000`）。这类输出一旦入库，医生会看到模板文字、评测会被污染。

**本模块只做"识别"**（纯函数、零依赖）：命中即由调用方**拒收并降级**（转人工复核），
不改 prompt、不改任何阈值 —— 修 prompt 属于另一个决策（A.70.1 的方案 A/C）。
"""

from __future__ import annotations

import re

#: 模板里的假 chunk ID（`chunk_a1b2` 形态；真 ID 一定带 `#数字` 后缀或 `kb_v...:` 前缀）
_PLACEHOLDER_CHUNK = re.compile(r"chunk_[a-z0-9]{4}\b")

#: 模板里的假病人 ID
_PLACEHOLDER_PATIENT = re.compile(r"PATIENT_\d+")

#: 模板的「输出」段标题（正常结论不会出现这个 markdown 标题）
_OUTPUT_HEADER = re.compile(r"#\s*输出")

#: 命中几次「# 输出」才算复述（正常结论里出现一次都罕见；两次以上必是照抄）
_OUTPUT_HEADER_MIN = 2


def detect_prompt_echo(text: str) -> str | None:
    """判断模型输出是否在复述提示词示例。纯函数。

    Returns:
        命中时返回**原因码**（用于日志与 trace），未命中返回 ``None``：

        * ``placeholder_chunk_id`` —— 出现 `chunk_xxxx` 这类占位证据 ID；
        * ``placeholder_patient_id`` —— 出现 `PATIENT_001` 这类占位病人；
        * ``repeated_output_header`` —— `# 输出` 出现 ≥2 次（同一段被反复复述）。
    """
    t = text or ""
    if _PLACEHOLDER_CHUNK.search(t):
        return "placeholder_chunk_id"

    # ⚠️ A.77 更正：`PATIENT_001` **单独出现不算模板回显**。
    # 真机证据：库里 22 个知识块含 `PATIENT_` 标记（对话语料脱敏时用 `PATIENT_001` 顶替患者称呼），
    # 模型在"患者解释"段落会把它抄出来（如 `患者PATIENT_001: 解释: …`），
    # 而三份 prompt 里**根本没有**这个串（`grep PATIENT_001 app/agents/prompts/*.md` = 0）。
    # 首版规则把"只命中 PATIENT_"当回显 → 660 条已删记录里 **415 条是误判**（它们是有分级、
    # 有鉴别诊断的真结论，只是引用了 `chunk_1` 这类假 ID）。
    # 现在：PATIENT_ 仅在与"确凿回显特征"同时出现时才计入；单独出现交
    # `detect_patient_token_leak()` 报**另一个缺陷**（语料脱敏标记外泄），不阻断输出。
    headers = len(_OUTPUT_HEADER.findall(t))
    if _PLACEHOLDER_PATIENT.search(t) and (headers >= 1 or "chunk_" in t):
        return "placeholder_patient_id"
    # A.150：`# 输出` 重复本身**不足以判回显** —— 真机实测（G21 双采样，4 例全中）：
    # Qwen2.5-7B 在含 "# 输出示例" 的 prompt 下，即使完全没复述示例、结论里分级/理由
    # 齐全且有效，也会把 "# 输出" 当格式标记反复输出（7~15 次）。
    # 于是整批被拒 → 双采样/一致率全部采不到数。
    # 修正：重复标题 **且** 同时含模板回显的实义特征（占位 chunk_id 或占位病人）才算；
    # "只有标题重复、结论本身有真内容" 交由下游分级解析决定成败。
    if headers >= _OUTPUT_HEADER_MIN and (_PLACEHOLDER_CHUNK.search(t) or _PLACEHOLDER_PATIENT.search(t)):
        return "repeated_output_header"
    return None


def detect_patient_token_leak(text: str) -> bool:
    """语料脱敏标记外泄：答案里出现 `PATIENT_001` 这类**语料占位符**（不是模板回显）。

    单独成函数是因为**处置不同**：这类输出内容本身可用（有分级、有鉴别诊断），
    只是把语料的脱敏标记抄进了患者解释 → 应当**清洗替换**（如替换为「患者」），
    而不是像模板回显那样整条拒收。

    ## A.91：这个函数此前**全仓零调用方**（又一处"实现了但没接线"）

    A.77（2026-10-21）就写好了它并明确"处置是清洗，不是拒收"，但之后
    **没有任何生产路径调用它** —— 于是：
    * 模型持续把 `患者PATIENT_001:` / `PATIENT_061医生` 抄进结论；
    * 安全网关把它误判成"患者姓名"→ 整条结论降级抑制（A.89.4 才修掉误判）；
    * 历史库积累 47 条，A.90 手工清洗了一次（**只治标**）。

    只清历史不阻断生成侧，等于每次新判读都会重新污染 —— A.91 真机实测：
    清洗后跑一条新判读，`PATIENT_` **立刻又出现**。所以现在把它接进
    `specialist/base.py` 的 4b 步（与 `detect_prompt_echo` 同一位置），
    **落库之前**清洗。
    """
    return bool(_PLACEHOLDER_PATIENT.search(text or ""))


#: 语料标记的三种"被当人名用"形态（与 A.90 的 p63 清洗脚本同一口径）
_RE_LEAK_WITH_COLON = re.compile(r"(?:患者|病人|被检者)\s*PATIENT_\d+\s*[：:]")
_RE_LEAK_AS_TITLE = re.compile(r"PATIENT_\d+\s*(?=(?:医生|主任|教授|护士|技师|大夫|院长))")
_RE_LEAK_BARE = re.compile(r"PATIENT_\d+")


def clean_patient_token_leak(text: str) -> tuple[str, int]:
    """把语料脱敏标记**清洗成自然表述**（A.91 接线；与 A.90 的 p63 同一规则）。

    为什么清洗而不是拒收（A.77 教训）：这类结论**有分级、有鉴别诊断**，
    只是把语料占位符抄进了"患者解释"段落。整条拒收会丢掉真实结论 ——
    A.77 已经因为"把 PATIENT_ 当回显铁证"误删过 415 条。

    清洗规则（幂等）：
    * ``患者PATIENT_001:`` → ``患者：``
    * ``PATIENT_061医生`` → ``医生``
    * 其余裸 ``PATIENT_00N`` → ``患者``

    Returns:
        ``(cleaned_text, hit_count)``。``hit_count == 0`` 表示原文干净（原样返回）。
    """
    if not text:
        return text, 0
    hits = 0
    out = text
    out, n = _RE_LEAK_WITH_COLON.subn("患者：", out)
    hits += n
    out, n = _RE_LEAK_AS_TITLE.subn("", out)
    hits += n
    out, n = _RE_LEAK_BARE.subn("患者", out)
    hits += n
    return out, hits


def has_real_evidence_id(text: str, evidence_ids: list[str]) -> bool:
    """输出里是否引用了**本次真实证据**的 ID（用于"引用可信度"诊断，不作拦截条件）。

    只做精确子串匹配：真实 ID 形如 `kb_v1.3.0:pdf_x_0001#0000`，被模型原样引用才算。
    """
    return any(ev and ev in (text or "") for ev in evidence_ids or [])


#: 「只有分级码、没有正文」的形态（2026-11-08 加）。
#:
#: 生产实测：**32.50% 的输入**产出了这种退化结论，例如 `甲状腺 · TR1` / `TI-RADS 4a`。
#: 根因是 II-Medical-8B 的 chat template 不读 `enable_thinking`，token 全花在 `<think>` 内，
#: `finish_reason=length`，引擎剥除后正文为空，只剩分级码。
#:
#: 旧的 `output_guard` **不拦**这种形态（它既不空、也不是回显），于是静默落库，
#: 让"分级一致率"等指标把"模型没写正文"当成"模型不稳定"来测。
_DEGENERATE_GRADE_ONLY = re.compile(
    r"^\s*(?:甲状腺|肝脏|心脏)?\s*[·:：\-]?\s*(?:TI-?RADS|LI-?RADS|TR|LR)?\s*"
    r"(?:[1-6][abc]?|正常|无异常)\s*$",
    re.IGNORECASE,
)


def detect_degenerate_grade_only(text: str, *, min_body_chars: int = 6) -> str | None:
    """判断结论是否是「只有分级码、没有正文」的退化形态。纯函数。

    为什么需要一个独立检测：
    这种输出**既不空**（有 `TR1`）、**也不是回显**（没有占位 ID），
    所以 `detect_prompt_echo()` 和"空文本"检查都放它过去，它静默落库，
    让「分级一致率」把「模型没写正文」当成「模型不稳定」来度量。

    判定逻辑（2026-11-08 修正）：
    1. 整串**只由分级码与分隔符组成** → 退化（``_DEGENERATE_GRADE_ONLY`` 全匹配）
    2. 否则，去掉分级码后剩余正文 < ``min_body_chars`` → 退化

    **不能只靠第 2 条**：像 `TI-RADS 3，建议复查` 这样的短结论，
    正则因尾部有内容而不匹配整串，但"去标点后长度 < 20"仍会误杀它
    —— 而它有实质建议，不是退化。假阳性（误杀真结论）比漏报更糟。

    Args:
        text: 模型结论正文。
        min_body_chars: 去分级码后剩余正文的字符下限。**默认 6**：
            临床短建议（`建议复查`=4字、`建议穿刺`=4字、`随访`=2字）都很常见，
            阈值定 20 会把它们全误杀。6 字足以区分「有建议」与「只有码」。

    Returns:
        命中返回原因码 ``"grade_only_no_body"``，否则 ``None``。

    Examples:
        >>> detect_degenerate_grade_only("甲状腺 · TR1")
        'grade_only_no_body'
        >>> detect_degenerate_grade_only("TI-RADS 4a 类，结节 8mm，建议 6 个月复查")
        >>> detect_degenerate_grade_only("") is None
        True
    """
    t = (text or "").strip()
    if not t:
        # 完全空是另一类缺陷（由 finish_reason=='length_empty' 报），不在这里重复判
        return None

    # 第 1 条：整串就是分级码（允许器官前缀与分隔符）
    if _DEGENERATE_GRADE_ONLY.match(t):
        return "grade_only_no_body"

    # 第 2 条：有尾部内容，但去掉分级码后正文太短。
    # 先剥掉开头的分级码片段，再看剩下的可读字符数。
    head = re.match(
        r"^\s*(?:甲状腺|肝脏|心脏)?\s*[·:：\-]?\s*"
        r"(?:TI-?RADS|LI-?RADS|TR|LR)?\s*[1-6][abc]?\s*[类]?\s*[，,。.：:]?\s*",
        t,
        re.IGNORECASE,
    )
    rest = t[head.end():] if head else t
    body = re.sub(r"[\s·:：\-=,，。;；]+", "", rest)
    if len(body) < min_body_chars:
        return "grade_only_no_body"
    return None


#: 「分数区间 → 分级」映射的两种书写形态（2026-11-08 加，2026-11-09 改判据）。
#:
#: 生产实测：模型会输出
#:   `（注：0-2 分为 TR2）`
#:   `（注：0-2分为TR2，3-4分为TR3）`
#:
#: **2026-11-09 改判据（本轮实测推翻旧口径）**：旧实现只要正文出现这个模式就判"自造"，
#: 但**生产 prompt 里本来就有这张表**（`app/agents/prompts/thyroid.md` 的
#: 「TI-RADS 5 等级标准」段：`TR1（0 分）` / `TR2（2 分）` / `TR4（4-6 分）` / `TR5（≥ 7 分）`）。
#: 于是模型把 prompt 自带的规则复述一遍也会被判成"编造" —— 全库 797 条有正文的结论里
#: 命中 39 条，逐条核对后 **40/45 条映射其实与 prompt 表格完全一致**（P.214 实测）。
#:
#: 现在的判据是**语义比对**而不是"字符串是否存在"：
#:   * 正文里 `分数区间 → 分级` 与来源（prompt + 检索块）中的映射**一致** → 支撑，放行；
#:   * 同分级但**区间不同**（如 `0-2 分为 TR2`，而来源是 `TR1（0 分）`+`TR2（2 分）`）→ 矛盾，命中；
#:   * 来源里**根本没有**这个分级 → 无支撑，命中。
#:
#: 真幻觉仍在：`0-2 分为 TR2` 把 0 分并进 TR2，与 prompt 的 `TR1（0 分）` 直接冲突
#: —— 这正是 4.2 里那 3 条"编造规则"的形态。
#: **必须带分级前缀或「类」后缀**才算一条"分档规则"。
#:
#: 为什么（2026-11-09 真机假阳性）：评分算式 `钙化 1 分 + 低回声 2 分 + 形状不规则 2 分
#: = 5 分 → TR3` 里，`2 分 = 5` 会被当成"2 分对应 TR5" —— 因为裸数字也被接受为分级。
#: 结果是一条**正常算分**的结论被判成"编造规则"。真编造（`0-2 分为 TR2`）和
#: 既有判据里的其它形态（`3-4分对应TR4a` / `1分是4A类`）都带前缀或「类」，
#: 所以收紧成"前缀或类二者必居其一"既堵住假阳性、又不影响真阳性。
_RULE_ARROW = re.compile(
    r"(\d+)\s*(?:[~\-–至到]\s*(\d+))?\s*分\s*[，,]?\s*(?:为|对应|=|是|归|进|划)\s*"
    r"(?:(TR|LR|TI-?RADS|LI-?RADS)\s*([1-6])([abcABC])?\s*类?"
    r"|([1-6])([abcABC])?\s*类)",
    re.IGNORECASE,
)

#: 来源侧（prompt 模板）的表格形态：`**TR2（2 分）**` / `**TR4（4-6 分）**` / `**TR5（≥ 7 分）**`
_RULE_TABLE = re.compile(
    r"(TR|LR|TI-?RADS|LI-?RADS)\s*([1-6])([abcABC])?\s*[（(]\s*(≥|>=|>|≤|<=)?\s*"
    r"(\d+)\s*(?:[~\-–至到]\s*(\d+))?\s*分\s*[）)]",
    re.IGNORECASE,
)

#: `(下限, 上限, 分级)`；上限用 ``math.inf`` 表示"及以上"
GradingRule = tuple[float, float, str]


def _canonical_grade(prefix: str | None, number: str, suffix: str | None) -> str:
    """归一成 ``TR4a`` / ``LR5`` 这种比较键（``TI-RADS 4a`` 与 ``TR4a`` 视为同一档）。"""
    p = (prefix or "TR").upper().replace("-", "")
    if p == "TIRADS":
        p = "TR"
    elif p == "LIRADS":
        p = "LR"
    return f"{p}{number}{(suffix or '').lower()}"


def extract_grading_rules(text: str) -> list[GradingRule]:
    """从**模型输出**里抽出 `分数区间 → 分级` 映射。纯函数。

    Examples:
        >>> extract_grading_rules("（注：0-2 分为 TR2）")
        [(0.0, 2.0, 'TR2')]
        >>> extract_grading_rules("= 4 分 → TR4（4-6 分为 TR4）")
        [(4.0, 6.0, 'TR4')]
    """
    out: list[GradingRule] = []
    for m in _RULE_ARROW.finditer(text or ""):
        lo = float(m.group(1))
        hi = float(m.group(2)) if m.group(2) else lo
        if m.group(3):  # 带前缀：`0-2 分为 TR2`
            rule = _canonical_grade(m.group(3), m.group(4), m.group(5))
        else:  # 仅「类」后缀：`1分是4A类`
            rule = _canonical_grade(None, m.group(6), m.group(7))
        out.append((lo, hi, rule))
    return out


def extract_grading_rules_from_source(text: str) -> list[GradingRule]:
    """从**来源**（prompt 模板 / 知识块）里抽出 `分级 → 分数区间` 表格映射。纯函数。

    与 :func:`extract_grading_rules` 的区别：来源侧是表格形态（`TR2（2 分）`），
    模型侧是箭头形态（`2 分为 TR2`）。两者都归一成 `(下限, 上限, 分级)` 才能比对。

    Examples:
        >>> extract_grading_rules_from_source("- **TR2（2 分）**：不怀疑恶性")
        [(2.0, 2.0, 'TR2')]
        >>> extract_grading_rules_from_source("- **TR5（≥ 7 分）**：高度可疑")
        [(7.0, inf, 'TR5')]
    """
    out: list[GradingRule] = []
    for m in _RULE_TABLE.finditer(text or ""):
        op = m.group(4)
        a = float(m.group(5))
        b = m.group(6)
        grade = _canonical_grade(m.group(1), m.group(2), m.group(3))
        if b:
            out.append((a, float(b), grade))
        elif op in ("≥", ">=", ">"):
            out.append((a, float("inf"), grade))
        elif op in ("≤", "<=", "<"):
            out.append((0.0, a, grade))
        else:
            out.append((a, a, grade))
    return out


def _rule_support(rule: GradingRule, source_rules: list[GradingRule]) -> str:
    """单条映射与来源的关系：``supported`` / ``contradicted`` / ``unsupported``。"""
    lo, hi, grade = rule
    same_grade = [r for r in source_rules if r[2] == grade]
    if not same_grade:
        return "unsupported"
    for s_lo, s_hi, _ in same_grade:
        if (lo, hi) == (s_lo, s_hi):
            return "supported"
        # 来源是单点（`TR2（2 分）`）而模型给了区间（`2-2 分`）—— 等价
        if s_lo == s_hi and lo == hi == s_lo:
            return "supported"
        # 区间被来源覆盖（`TR5（≥7 分）` 覆盖 `7-10 分为 TR5`）
        if s_lo <= lo and hi <= s_hi:
            return "supported"
    return "contradicted"


def detect_self_made_grading_rule(
    text: str,
    *,
    kb_has_rule: bool = False,
    source_text: str = "",
) -> str | None:
    """判断结论里是否出现「模型自造的分档规则」。纯函数。

    ## 为什么这是一个独立的拒绝理由

    模型输出 `（注：0-2 分为 TR2）` 时：
    - 它**不是空**（`detect_degenerate_grade_only` 不管）
    - 它**不是回显**（`detect_prompt_echo` 不管，没有占位 ID）
    - 但它**给出了一个具体的数值分档规则**，而来源里没有这条

    这种「编造规则」比编造事实更危险 —— 事实可以核对，**规则会被当成方法论复用**。

    ## 判据是"与来源矛盾/无支撑"，不是"字符串是否出现过"

    2026-11-09 修正（P.214 实测）：旧实现只做模式匹配，而生产 prompt 里**本来就有**
    分档表 → 模型复述 prompt 也会被判成编造（全库 39 条命中，40/45 条映射其实与 prompt
    一致）。现在必须比对**语义**：同分级但区间不同（`0-2 分为 TR2` vs prompt 的
    `TR1（0 分）`+`TR2（2 分）`）= 矛盾，才算自造。

    Args:
        text: 模型结论正文。
        kb_has_rule: 调用方已确认来源里**确有**对应规则 → 直接放行（快速通道）。
            **调用方必须真的去查**，不能默认传 True。
        source_text: 本次实际喂给模型的来源（prompt 模板 + 检索块正文）。
            给了就按语义比对；**空字符串**表示调用方拿不到来源 → 退化为
            "只要出现映射就命中的严格模式"（宁可报，不可漏 —— 但调用方应尽量传）。

    Returns:
        命中返回 ``"self_made_grading_rule"``，否则 ``None``。

    Examples:
        >>> detect_self_made_grading_rule("结节2分，归TR3类")
        'self_made_grading_rule'
        >>> detect_self_made_grading_rule("结节2分，归TR3类", kb_has_rule=True)
        >>> detect_self_made_grading_rule("（注：0-2 分为 TR2）",
        ...     source_text="- **TR1（0 分）**：良性\\n- **TR2（2 分）**：随访")
        'self_made_grading_rule'
        >>> detect_self_made_grading_rule("（注：4-6 分为 TR4）",
        ...     source_text="- **TR4（4-6 分）**：中度可疑")
    """
    t = text or ""
    if not t or kb_has_rule:
        return None
    rules = extract_grading_rules(t)
    if not rules:
        return None
    if not source_text:
        # 拿不到来源 → 严格模式：出现映射即命中（旧行为，保证不静默放过）
        return "self_made_grading_rule"
    source_rules = extract_grading_rules_from_source(source_text)
    for rule in rules:
        if _rule_support(rule, source_rules) != "supported":
            return "self_made_grading_rule"
    return None


# ----------------------------------------------------------------------
# 2026-11-09：多套权威语料并存时，结论是否**只引了一套**
# ----------------------------------------------------------------------

#: 结论里"点名依据"的写法：``依据《X》`` / ``根据 X`` / ``参照《X》`` / ``按 X 标准``。
#:
#: 只认**书名号与显式引导词**两种，不做模糊匹配 —— 模糊匹配会把
#: "根据结节大小"这种普通句子当成引用，从而误判"已标注来源"。
_CITATION_PATTERNS = (
    re.compile(r"依据\s*《([^》]{1,40})》"),
    re.compile(r"参照\s*《([^》]{1,40})》"),
    re.compile(r"按\s*《([^》]{1,40})》"),
    re.compile(r"根据\s*《([^》]{1,40})》"),
    re.compile(r"依据\s*(?:中华医学会[^，。；\n]{0,20}|国家卫健委|国家卫生健康委员会)"),
    re.compile(r"根据\s*(?:中华医学会[^，。；\n]{0,20}|国家卫健委|国家卫生健康委员会)"),
)


def cited_source_labels(text: str) -> list[str]:
    """抽出结论里**点名引用**的来源标签（纯函数）。

    返回的是原文里出现的名字（如 ``C-TIRADS 2021`` / ``甲状腺癌诊疗规范 2018``），
    不做归一 —— 归一交给调用方（``app/knowledge/source_set.py``），
    因为归一规则会随语料增加而变，判据模块不该跟着改。
    """
    t = text or ""
    if not t:
        return []
    out: list[str] = []
    for pattern in _CITATION_PATTERNS:
        for m in pattern.finditer(t):
            label = (m.group(1) if m.groups() else m.group(0) or "").strip()
            if label and label not in out:
                out.append(label)
    return out


def detect_single_source_conclusion(
    text: str,
    *,
    available_labels: list[str] | None = None,
    conflict_fact_keys: list[str] | None = None,
) -> str | None:
    """判断结论是否**只引用了一套语料**，而证据里明明有多套。纯函数。

    ## 为什么这是一个独立的**诊断**（而不是拒收理由）

    多套权威语料并存、且口径确有分歧时（``conflict_fact_keys`` 非空），
    模型如果只写"依据《C-TIRADS 2021》，>15 mm 建议 FNA"，医生会以为
    **这是唯一口径**。但系统知道还有一套诊疗规范给的是 >1cm。

    这种输出的问题不是"错"，而是**不完整且有误导性** —— 它藏掉了一个
    医生有权知道的分歧。处置是**标记 + 提示**，不是拒收：
    A.77 的教训是"用单一标记定罪"误删过 415 条真结论，这里不重犯。

    Args:
        text: 模型结论正文。
        available_labels: 本次证据里**真实存在**的语料来源标签
            （调用方从 ``chunks`` 现算，不许写死）。为空或只有 1 个 → 不判
            （单来源时"只引一套"是正确行为）。
        conflict_fact_keys: 本次检测到的**确有分歧**的事实键
            （``source_conflict.ConflictReport.conflicts[].fact_key``）。
            为空 → 不判（没有分歧时"只引一套"完全正当）。

    Returns:
        命中返回 ``"single_source_on_conflict"``，否则 ``None``。

    Examples:
        >>> detect_single_source_conclusion(
        ...     "依据《C-TIRADS 2021》，4A 类 >15 mm 建议 FNA",
        ...     available_labels=["C-TIRADS 2021", "甲状腺癌诊疗规范 2018"],
        ...     conflict_fact_keys=["fna_threshold"],
        ... )
        'single_source_on_conflict'
    """
    t = text or ""
    if not t:
        return None
    labels = [s for s in (available_labels or []) if s]
    # 少于两套来源 / 没有实际分歧 → "只引一套"是正当的，不判。
    if len(labels) < 2 or not (conflict_fact_keys or []):
        return None
    cited = cited_source_labels(t)
    if len(cited) >= 2:
        return None
    # 命中数 0 也要报：既没并列、又没有任何点名来源，问题比只引一套更严重。
    return "single_source_on_conflict"
