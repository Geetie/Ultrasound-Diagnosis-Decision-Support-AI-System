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
