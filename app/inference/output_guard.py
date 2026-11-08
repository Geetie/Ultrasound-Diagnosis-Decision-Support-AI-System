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


#: 「自造分档规则」的形态（2026-11-08 加）。
#:
#: 生产实测：模型会输出
#:   `（注：0-2 分为 TR2）`
#:   `（注：0-2分为TR2，3-4分为TR3）`
#: 而 `kb_v1.4.0` 甲状腺 601 块与 `thyroid.md` 对 `"0-2 分"` **各 0 命中**
#: —— > 这是模型自己编的分档规则，不是知识库内容。
#:
#: 15 条候选**全部来自 2026-10-25~11-07 的新判读**（旧模型期 0 条）→ 现网仍在犯。
_SELF_MADE_GRADING_RULE = re.compile(
    r"(?:注|备注)?\s*[:：]?\s*"
    r"\d+(?:\s*[~\-–至到]\s*\d+)?\s*分\s*[，,]?\s*(?:为|对应|=|是|归|进|划)\s*"
    r"(?:TR|LR|TI-?RADS|LI-?RADS)?\s*[1-6][abcABC]?\s*[类]?",
    re.IGNORECASE,
)


def detect_self_made_grading_rule(
    text: str, *, kb_has_rule: bool = False
) -> str | None:
    """判断结论里是否出现「模型自造的分档规则」。纯函数。

    ## 为什么这是一个独立的拒绝理由

    模型输出 `（注：0-2 分为 TR2）` 时：
    - 它**不是空**（`detect_degenerate_grade_only` 不管）
    - 它**不是回显**（`detect_prompt_echo` 不管，没有占位 ID）
    - 但它**给出了一个具体的数值分档规则**，而这个规则在知识库里**不存在**

    这种「编造规则」比编造事实更危险 —— 事实可以核对，**规则会被当成方法论复用**。

    Args:
        text: 模型结论正文。
        kb_has_rule: 知识库 / 检索 chunk 里**是否确实有**对应分档规则。
            为 ``True`` 时不判（模型引用的是真规则）。
            **调用方必须真的去查**，不能默认传 True。

    Returns:
        命中返回 ``"self_made_grading_rule"``，否则 ``None``。

    Examples:
        >>> detect_self_made_grading_rule("结节2分，归TR3类")
        'self_made_grading_rule'
        >>> detect_self_made_grading_rule("结节2分，归TR3类", kb_has_rule=True)
        >>> detect_self_made_grading_rule("TI-RADS 4a 类，建议复查")
    """
    t = text or ""
    if not t or kb_has_rule:
        return None
    if _SELF_MADE_GRADING_RULE.search(t):
        return "self_made_grading_rule"
    return None
