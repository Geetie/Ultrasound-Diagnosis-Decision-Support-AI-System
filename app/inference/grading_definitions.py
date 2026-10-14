import re
import threading

_GRADING_PATTERNS: dict[str, list[re.Pattern]] = {
    # 2026-09-29 真机 780 例评测：61 例提取失败的实际输出形态——
    # 专项 prompt 输出 "TR4a"（而非 TI-RADS 4a）、"**分级: 4a**"（结构化行）、
    # "3 级（TI-RADS）"（数字级+括号体系名）、"TI-RADS: 4a"（冒号分隔）。
    # 新模式一律放在列表前部（精确优先）；既有模式与反例守卫
    # （"随访3月"/"大小约3mm" 不得误抽）保持不动。
    "thyroid": [
        # 结构化/分级行：{"grading": "4a"}、grading: TR4a、分级: 4a、分级: TR 4a
        re.compile(
            r'["\']?grading["\']?\s*[:：]\s*["\']?(?:TR\s*[-_ ]?)?(\d[a-zA-Z]?)["\']?',
            re.IGNORECASE,
        ),
        re.compile(r'分级\s*[:：]\s*(?:TR\s*[-_ ]?)?(\d[a-zA-Z]?)', re.IGNORECASE),
        # TR 码（专项 prompt 的输出格式，与 TI-RADS 数字码同义）
        re.compile(r'\bTR\s*[-_ ]?\s*(\d[a-zA-Z]?)\b', re.IGNORECASE),
        # "TI-RADS: 4a" / "TI-RADS grade 3"（冒号/英文 grade 分隔，原模式吃不到）
        re.compile(
            r'TI[-_]?RADS\s*(?:grade|分级|级别)?\s*[:：]\s*(\d[a-zA-Z]?)\b',
            re.IGNORECASE,
        ),
        # "3 级（TI-RADS）"（数字级在前、体系名在括号里）
        re.compile(r'(\d[a-zA-Z]?)\s*级\s*[（(]\s*TI[-_]?RADS', re.IGNORECASE),
        re.compile(r'TI[-_]?RADS\s*(\d[a-zA-Z]?)', re.IGNORECASE),
        re.compile(r'TI[-_]?RADS[^\d]*?(\d[a-zA-Z]?)\s*(?:类|级|category)', re.IGNORECASE),
        re.compile(r'RADS\s*(\d[a-zA-Z]?)\s*(?:类|级)', re.IGNORECASE),
        re.compile(r'甲状腺[^。]*?(?:TI[-_]?RADS|结节|占位|病变)[^。]*?(\d[a-zA-Z]?)\s*类', re.IGNORECASE),
        re.compile(r'甲状腺[^。]*?结节[^。]*?(\d[a-zA-Z]?)\s*类(?!别)', re.IGNORECASE),
        re.compile(r'分类\s*(\d[a-zA-Z]?)\s*(?:类|级)?', re.IGNORECASE),
    ],
    "liver": [
        # 结构化/分级行：{"grading": "LR-3"}、grading: 3、分级: LR 3
        re.compile(
            r'["\']?grading["\']?\s*[:：]\s*["\']?(?:LR\s*[-_ ]?|LI[-_]?RADS\s*)?(\d[a-zA-Z]?)["\']?',
            re.IGNORECASE,
        ),
        re.compile(r'分级\s*[:：]\s*(?:LR\s*[-_ ]?)?(\d[a-zA-Z]?)', re.IGNORECASE),
        # LR3 / LR-3 / LR 3（LR-TR 变体已有模式覆盖）
        re.compile(r'\bLR\s*[-_ ]?\s*(\d[a-zA-Z]?)\b', re.IGNORECASE),
        # "LI-RADS: 3"（冒号分隔）
        re.compile(
            r'LI[-_]?RADS\s*(?:grade|分级|级别)?\s*[:：]\s*(\d[a-zA-Z]?)\b',
            re.IGNORECASE,
        ),
        # "3 级（LI-RADS）"
        re.compile(r'(\d[a-zA-Z]?)\s*级\s*[（(]\s*LI[-_]?RADS', re.IGNORECASE),
        re.compile(r'LI[-_]?RADS\s*(\d[a-zA-Z]?)', re.IGNORECASE),
        re.compile(r'LI[-_]?RADS[^\d]*?(\d[a-zA-Z]?)\s*(?:类|级|category)', re.IGNORECASE),
        re.compile(r'LR[-_]?TR\s*(\w+)', re.IGNORECASE),
        re.compile(r'肝脏[^。]*?(?:LI[-_]?RADS|占位|病变|结节|肿块)[^。]*?(\d[a-zA-Z]?)\s*类', re.IGNORECASE),
        re.compile(r'肝脏[^。]*?(?:占位|病变|结节|肿块)[^。]*?(\d[a-zA-Z]?)\s*类', re.IGNORECASE),
        re.compile(r'分类\s*(\d[a-zA-Z]?)\s*(?:类|级)?', re.IGNORECASE),
    ],
    "cardiac": [
        # 结构化行：{"grading": "mild"}、grading: mildly_reduced、分级: mild
        re.compile(
            r'["\']?grading["\']?\s*[:：]\s*["\']?(正常|轻度|中度|重度|normal|mild|moderate|severe|mildly[ _-]?reduced|moderately[ _-]?reduced|severely[ _-]?reduced)["\']?',
            re.IGNORECASE,
        ),
        # 英文表述（专项 prompt 输出 mildly_reduced 等，原模式只认中文）
        re.compile(
            r'\b(mildly[ _-]?reduced|moderately[ _-]?reduced|severely[ _-]?reduced)\b',
            re.IGNORECASE,
        ),
        re.compile(r'\b(normal|mild|moderate|severe)\b', re.IGNORECASE),
        re.compile(r'(轻度|中度|重度)\s*(?:减低|降低|异常|功能障碍|受损)?', re.IGNORECASE),
        re.compile(r'(正常)(?!减低|降低|异常|功能障碍|受损)', re.IGNORECASE),
        re.compile(r'LVEF\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%', re.IGNORECASE),
        re.compile(r'心功能\s*(?:分级[为：:]?)?\s*(Ⅰ|1)\s*级', re.IGNORECASE),
        re.compile(r'心功能\s*(?:分级[为：:]?)?\s*(Ⅱ|2)\s*级', re.IGNORECASE),
        re.compile(r'心功能\s*(?:分级[为：:]?)?\s*(Ⅲ|3)\s*级', re.IGNORECASE),
        re.compile(r'心功能\s*(?:分级[为：:]?)?\s*(Ⅳ|4)\s*级', re.IGNORECASE),
    ],
    "breast": [
        re.compile(r'BI[-_]?RADS\s*(\d[a-zA-Z]?)', re.IGNORECASE),
        re.compile(r'BI[-_]?RADS[^\d]*?(\d[a-zA-Z]?)\s*(?:类|级|category)', re.IGNORECASE),
        re.compile(r'RADS\s*(\d[a-zA-Z]?)\s*(?:类|级)', re.IGNORECASE),
        re.compile(r'乳腺[^。]*?(?:BI[-_]?RADS|结节|占位|病变|肿块)[^。]*?(\d[a-zA-Z]?)\s*类', re.IGNORECASE),
        re.compile(r'乳腺[^。]*?(?:结节|占位|病变|肿块)[^。]*?(\d[a-zA-Z]?)\s*类', re.IGNORECASE),
        re.compile(r'分类\s*(\d[a-zA-Z]?)\s*(?:类|级)?', re.IGNORECASE),
    ],
}

_GRADE_HIERARCHY: dict[str, dict[str, tuple[int, int]]] = {
    "thyroid": {
        "0": (0, 0), "1": (1, 0), "2": (2, 0), "3": (3, 0),
        "4": (4, 0), "4a": (4, 1), "4b": (4, 2), "4c": (4, 3), "5": (5, 0),
    },
    "liver": {"0": (0, 0), "1": (1, 0), "2": (2, 0), "3": (3, 0), "4": (4, 0), "5": (5, 0), "lr-tr": (4, 0)},
    "breast": {
        "0": (0, 0),
        "1": (1, 0), "2": (2, 0), "3": (3, 0),
        "4": (4, 0), "4a": (4, 1), "4b": (4, 2), "4c": (4, 3), "5": (5, 0),
    },
    "cardiac": {"normal": (0, 0), "mild": (1, 0), "moderate": (2, 0), "severe": (3, 0)},
}

# LVEF→分级切点（normal/mild/moderate/severe）：超声报告常用的收缩功能
# 减低程度分级惯例（≥55 / 45~54 / 30~44 / <30），与 ASE 及《中国心力衰竭
# 诊断和治疗指南》的 HFrEF（LVEF≤40%）/HFmrEF（41~49%）口径兼容但不等价。
# 可经 verification_config 的 cardiac LVEF 区间覆写（_sync_lvef_thresholds_from_config）；
# 上线前须由心脏超声专家复核确认本院口径。来源：清单自审 #5（2026-10-02 补注）。
_LVEF_TO_GRADING_DEFAULTS: list[tuple[float, str]] = [
    (55.0, "normal"),
    (45.0, "mild"),
    (30.0, "moderate"),
    (0.0, "severe"),
]

_lvef_cache: list[tuple[float, str]] | None = None
_lvef_cache_lock = threading.Lock()


def _get_lvef_grading_table() -> list[tuple[float, str]]:
    global _lvef_cache
    if _lvef_cache is not None:
        return _lvef_cache
    with _lvef_cache_lock:
        if _lvef_cache is not None:
            return _lvef_cache
        _lvef_cache = list(_LVEF_TO_GRADING_DEFAULTS)
        return _lvef_cache


def _sync_lvef_thresholds_from_config() -> None:
    global _lvef_cache
    try:
        from app.inference.verification_config import get_verification_config
        config = get_verification_config()
        cardiac_ranges = config.numerical_validation_config.organ_ranges.get("cardiac", [])
        lvef_range = next((r for r in cardiac_ranges if r.parameter == "LVEF"), None)
        if lvef_range is not None:
            new_table = [
                (lvef_range.max_value, "normal"),
                (55.0, "mild"),
                (lvef_range.min_value + 10.0, "moderate"),
                (lvef_range.min_value, "severe"),
            ]
            with _lvef_cache_lock:
                _lvef_cache = new_table
    except Exception:
        with _lvef_cache_lock:
            _lvef_cache = list(_LVEF_TO_GRADING_DEFAULTS)

_CN_CARDIAC_TO_GRADING: dict[str, str] = {
    "正常": "normal",
    "轻度": "mild",
    "中度": "moderate",
    "重度": "severe",
}

# 英文表述归一化（专项 prompt 输出 mildly_reduced 等；真机 780 例提取失败形态之一）
_EN_CARDIAC_TO_GRADING: dict[str, str] = {
    "normal": "normal",
    "mild": "mild",
    "moderate": "moderate",
    "severe": "severe",
    "mildly_reduced": "mild",
    "mildly reduced": "mild",
    "moderately_reduced": "moderate",
    "moderately reduced": "moderate",
    "severely_reduced": "severe",
    "severely reduced": "severe",
}

_CN_CARDIAC_CLASS_TO_GRADING: dict[str, str] = {
    "Ⅰ": "normal",
    "1": "normal",
    "Ⅱ": "mild",
    "2": "mild",
    "Ⅲ": "moderate",
    "3": "moderate",
    "Ⅳ": "severe",
    "4": "severe",
}

_CLINICALLY_SIGNIFICANT_SUBCLASS_PAIRS: dict[str, set] = {
    "thyroid": {frozenset({"4a", "4b"}), frozenset({"4b", "4c"})},
    "breast": {frozenset({"4a", "4b"}), frozenset({"4b", "4c"})},
    "liver": set(),
    "cardiac": set(),
}


def _map_lvef_to_grading(lvef_str: str) -> str:
    try:
        lvef = float(lvef_str)
    except (ValueError, TypeError):
        return lvef_str
    for threshold, label in _get_lvef_grading_table():
        if lvef >= threshold:
            return label
    return "severe"


def extract_grading(text: str, organ: str) -> str | None:
    result = extract_grading_with_conflict(text, organ)
    if result is None:
        return None
    return result[0]


_CARDIAC_SEVERITY_ORDER = {"normal": 0, "mild": 1, "moderate": 2, "severe": 3}


def _pick_lvef_preferred_grading(cn_grading: str, lvef_grading: str, prefer_lvef: bool = True) -> str:
    if prefer_lvef:
        return lvef_grading
    sev_cn = _CARDIAC_SEVERITY_ORDER.get(cn_grading, 0)
    sev_lvef = _CARDIAC_SEVERITY_ORDER.get(lvef_grading, 0)
    return cn_grading if sev_cn >= sev_lvef else lvef_grading


def extract_grading_with_conflict(text: str, organ: str) -> tuple[str, bool] | None:
    """从文本中提取分级结果，同时返回是否存在冲突。

    Returns:
        (grading, has_conflict) 元组，如果未找到分级返回None。
        has_conflict 在以下情况为True：
        - cardiac器官：LVEF数值与中文描述矛盾
        - 任何器官：文本中存在多个不同的分级结果
    """
    patterns = _GRADING_PATTERNS.get(organ, [])
    all_gradings: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            raw = match.group(1)
            if organ == "cardiac":
                raw_norm = raw.lower()
                if raw in _CN_CARDIAC_TO_GRADING:
                    all_gradings.append(_CN_CARDIAC_TO_GRADING[raw])
                elif raw_norm in _EN_CARDIAC_TO_GRADING:
                    all_gradings.append(_EN_CARDIAC_TO_GRADING[raw_norm])
                elif raw in _CN_CARDIAC_CLASS_TO_GRADING:
                    all_gradings.append(_CN_CARDIAC_CLASS_TO_GRADING[raw])
                else:
                    all_gradings.append(_map_lvef_to_grading(raw))
            else:
                all_gradings.append(raw.lower())
    if not all_gradings:
        return None
    first_grading = all_gradings[0]
    has_multi_conflict = len(set(all_gradings)) > 1
    if organ == "cardiac":
        cn_grading = None
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                raw = match.group(1)
                raw_norm = raw.lower()
                if raw in _CN_CARDIAC_TO_GRADING:
                    cn_grading = _CN_CARDIAC_TO_GRADING[raw]
                elif raw_norm in _EN_CARDIAC_TO_GRADING:
                    cn_grading = _EN_CARDIAC_TO_GRADING[raw_norm]
                elif raw in _CN_CARDIAC_CLASS_TO_GRADING:
                    cn_grading = _CN_CARDIAC_CLASS_TO_GRADING[raw]
                if cn_grading is not None:
                    lvef_grading = None
                    lvef_match = re.search(r'LVEF\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%', text, re.IGNORECASE)
                    if lvef_match:
                        lvef_grading = _map_lvef_to_grading(lvef_match.group(1))
                    has_lvef_conflict = lvef_grading is not None and lvef_grading != cn_grading
                    if has_lvef_conflict:
                        chosen = _pick_lvef_preferred_grading(cn_grading, lvef_grading, prefer_lvef=False)
                    else:
                        chosen = cn_grading
                    return (chosen, has_multi_conflict or has_lvef_conflict)
                lvef_grading = _map_lvef_to_grading(raw)
                return (lvef_grading, has_multi_conflict)
    return (first_grading, has_multi_conflict)


# ----------------------------------------------------------------------
# 专科结论层用的公开 API（2026-10-13 分级提取根治）
# ----------------------------------------------------------------------
# 背景：专科 Agent 原先各自维护一套平行正则，与评测/一致性链路（本模块
# ``_GRADING_PATTERNS`` / ``_GRADE_HIERARCHY``）**不同源**，导致同一份 LLM 输出
# 在"评测能抽到"而"生产结论抽不到"（`grade_unparseable`）。这里把两项能力
# 导出为公开 API，让专科只用单一事实源。


def grade_major_level(organ: str, grading: str | None) -> int | None:
    """分级 → **主级**整数（单一事实源：``_GRADE_HIERARCHY``）。

    - thyroid: ``"1".."5"`` / ``"4a"``/``"4b"``/``"4c"`` → 1..5（4a/4b/4c 主级均为 4）
    - liver:   ``"1".."5"`` / ``"lr-tr"``                 → 1..5
    - cardiac: ``normal``/``mild``/``moderate``/``severe`` → 0..3（严重度序）

    未知分级返回 ``None`` —— 调用方据此走兜底/降级，**不得**静默当成某个等级：
    「猜一个等级」比「如实说没解析出来」更危险（错误器官/错误分级的输出有临床后果）。
    """
    if not grading:
        return None
    entry = _GRADE_HIERARCHY.get(organ, {}).get(grading.strip().lower())
    return entry[0] if entry else None


def _severest_in(text: str, organ: str) -> str | None:
    """在给定片段内取最严重的分级（含 cardiac 的中英文/LVEF 归一化）。"""
    patterns = _GRADING_PATTERNS.get(organ, [])
    hierarchy = _GRADE_HIERARCHY.get(organ, {})
    best: tuple[tuple[int, int], str] | None = None
    for pattern in patterns:
        for match in pattern.finditer(text):
            raw = match.group(1)
            if organ == "cardiac":
                raw_norm = raw.lower()
                grading = (
                    _CN_CARDIAC_TO_GRADING.get(raw)
                    or _EN_CARDIAC_TO_GRADING.get(raw_norm)
                    or _CN_CARDIAC_CLASS_TO_GRADING.get(raw)
                    or _map_lvef_to_grading(raw)
                )
            else:
                grading = raw.lower()
            level = hierarchy.get(grading)
            if level is None:
                continue
            if best is None or level > best[0]:
                best = (level, grading)
    return best[1] if best else None


# ----------------------------------------------------------------------
# 结论正文的**机器字段标记**（单一事实源，2026-10-14）
# ----------------------------------------------------------------------
# 结论正文是一串「字段: 值」段。哪些字段是**系统派生出的决策**、哪些承载**依据/叙述**，
# 是两处共用的知识，必须只声明一次：
#   ① 分级抽取（本模块 :func:`extract_grading_severest`）→ 需要段边界；
#   ② 评测侧「口径 C：剔除派生字段后再判忠实度」（`scripts/langfuse/answer_modes.py`）
#      → 需要知道该剔哪些段。
# 两处各写一套必然走散（A.62 批 1 的现场：台账写着"第三种口径"，代码里根本没有该模式）。
#
#: 段**起锚点**：这些字段本身就是系统派生出的决策（模型的"分级"结论）。
FIELD_MARKERS_ANCHOR: tuple[str, ...] = ("分级", "grading")
#: 段内**派生决策**字段：同样是系统算出来的，不含任何知识库依据。
FIELD_MARKERS_INLINE: tuple[str, ...] = ("总分", "置信度", "score", "confidence")
#: 段**分隔/叙述**字段：它们承载"依据"（理由、证据、鉴别诊断…），**不得**当派生决策剔除。
FIELD_SEPARATORS: tuple[str, ...] = (
    "理由",
    "证据",
    "鉴别",
    "患者解释",
    "建议",
    "reason",
    "evidence",
    "differential",
)

# 显式分级段：`分级:` / `grading:` 之后到下一个字段名之前，是模型给出的**结论**。
# 与"全文最严重"并存的原因见 `extract_grading_severest` 的 docstring（实测假高教训）。
_EXPLICIT_GRADE_ANCHOR = re.compile(
    r"(?:^|[\s（(【\[])(?:" + "|".join(FIELD_MARKERS_ANCHOR) + r")\s*[:：]\s*",
    re.IGNORECASE,
)
_SEGMENT_STOP = re.compile(
    r"(?:" + "|".join(FIELD_MARKERS_INLINE + FIELD_SEPARATORS) + r")\s*[:：]",
    re.IGNORECASE,
)


def extract_grading_severest(text: str, organ: str) -> str | None:
    """抽取文本中最**严重**的分级（无匹配返回 ``None``）。

    两段式（2026-10-13 真机实测修正）：

    1. **显式分级段优先**：``分级:`` / ``grading:`` 之后、到下一个字段名
       （总分 / 置信度 / 理由 / 证据 / 鉴别 …）之前的那一段，是模型**给出的结论**；
       段内多码取重（"分级: TR3 或 TR4" → TR4，保守）。
    2. 没有显式分级段时，才**全文兜底**扫描取最重。

    为什么必须先看结论段：真机实测（p27）模型输出
    ``分级: TR3 总分: 3 … 理由: 实性(3) + 低回声(2) + 边界尚清(1) = 6 分 → TR4（注：4-6 分为 TR4）``
    —— 全文取最严重会把**理由里的范围说明（TR4）**当作结论，使报告分级被抬高一级
    （假高：给医生看的是 TR4，模型明明写的是 TR3）。这与"保守取重"不冲突：保守
    只在**同一结论段内**生效。

    与 :func:`extract_grading_with_conflict` 的区别：后者返回「第一个匹配」，
    供双引擎一致性比对（要的是"模型写了什么"）；本函数给**专科结论**用。
    """
    anchor = _EXPLICIT_GRADE_ANCHOR.search(text)
    if anchor:
        rest = text[anchor.end() :]
        stop = _SEGMENT_STOP.search(rest)
        segment = rest[: stop.start()] if stop else rest[:80]
        best = _severest_in(segment, organ)
        if best:
            return best
    return _severest_in(text, organ)
