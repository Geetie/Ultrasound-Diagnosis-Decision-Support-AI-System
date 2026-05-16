import re
import threading
from typing import Dict, List, Optional, Tuple


_GRADING_PATTERNS: Dict[str, List[re.Pattern]] = {
    "thyroid": [
        re.compile(r'TI[-_]?RADS\s*(\d[a-zA-Z]?)', re.IGNORECASE),
        re.compile(r'TI[-_]?RADS[^\d]*?(\d[a-zA-Z]?)\s*(?:类|级|category)', re.IGNORECASE),
        re.compile(r'RADS\s*(\d[a-zA-Z]?)\s*(?:类|级)', re.IGNORECASE),
        re.compile(r'甲状腺[^。]*?(?:TI[-_]?RADS|结节|占位|病变)[^。]*?(\d[a-zA-Z]?)\s*类', re.IGNORECASE),
        re.compile(r'甲状腺[^。]*?结节[^。]*?(\d[a-zA-Z]?)\s*类(?!别)', re.IGNORECASE),
        re.compile(r'分类\s*(\d[a-zA-Z]?)\s*(?:类|级)?', re.IGNORECASE),
    ],
    "liver": [
        re.compile(r'LI[-_]?RADS\s*(\d[a-zA-Z]?)', re.IGNORECASE),
        re.compile(r'LI[-_]?RADS[^\d]*?(\d[a-zA-Z]?)\s*(?:类|级|category)', re.IGNORECASE),
        re.compile(r'LR[-_]?TR\s*(\w+)', re.IGNORECASE),
        re.compile(r'肝脏[^。]*?(?:LI[-_]?RADS|占位|病变|结节|肿块)[^。]*?(\d[a-zA-Z]?)\s*类', re.IGNORECASE),
        re.compile(r'肝脏[^。]*?(?:占位|病变|结节|肿块)[^。]*?(\d[a-zA-Z]?)\s*类', re.IGNORECASE),
        re.compile(r'分类\s*(\d[a-zA-Z]?)\s*(?:类|级)?', re.IGNORECASE),
    ],
    "cardiac": [
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

_GRADE_HIERARCHY: Dict[str, Dict[str, Tuple[int, int]]] = {
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

_LVEF_TO_GRADING_DEFAULTS: List[Tuple[float, str]] = [
    (55.0, "normal"),
    (45.0, "mild"),
    (30.0, "moderate"),
    (0.0, "severe"),
]

_lvef_cache: Optional[List[Tuple[float, str]]] = None
_lvef_cache_lock = threading.Lock()


def _get_lvef_grading_table() -> List[Tuple[float, str]]:
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

_CN_CARDIAC_TO_GRADING: Dict[str, str] = {
    "正常": "normal",
    "轻度": "mild",
    "中度": "moderate",
    "重度": "severe",
}

_CN_CARDIAC_CLASS_TO_GRADING: Dict[str, str] = {
    "Ⅰ": "normal",
    "1": "normal",
    "Ⅱ": "mild",
    "2": "mild",
    "Ⅲ": "moderate",
    "3": "moderate",
    "Ⅳ": "severe",
    "4": "severe",
}

_CLINICALLY_SIGNIFICANT_SUBCLASS_PAIRS: Dict[str, set] = {
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


def extract_grading(text: str, organ: str) -> Optional[str]:
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


def extract_grading_with_conflict(text: str, organ: str) -> Optional[Tuple[str, bool]]:
    """从文本中提取分级结果，同时返回是否存在冲突。

    Returns:
        (grading, has_conflict) 元组，如果未找到分级返回None。
        has_conflict 在以下情况为True：
        - cardiac器官：LVEF数值与中文描述矛盾
        - 任何器官：文本中存在多个不同的分级结果
    """
    patterns = _GRADING_PATTERNS.get(organ, [])
    all_gradings: List[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            raw = match.group(1)
            if organ == "cardiac":
                if raw in _CN_CARDIAC_TO_GRADING:
                    all_gradings.append(_CN_CARDIAC_TO_GRADING[raw])
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
                if raw in _CN_CARDIAC_TO_GRADING:
                    cn_grading = _CN_CARDIAC_TO_GRADING[raw]
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
