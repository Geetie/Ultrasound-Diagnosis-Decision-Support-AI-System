"""M4 鉴别诊断清单解析。

## 为什么要有明确的格式约定

鉴别诊断是"按可能性排序的候选病种"，它的价值在于**顺序**与**支持/排除依据**。
自由文本（"鉴别诊断：结节性甲状腺肿、乳头状癌"）虽能读，但拿不到排序依据，
也无法在界面上分级展示。因此提示词里约定了管道分隔格式，本模块按该格式解析：

    鉴别诊断:
    1. 乳头状癌 | 0.60 | 支持: 微钙化、低回声 | 排除: 边界清晰
    2. 结节性甲状腺肿 | 0.30 | 支持: 混合回声 | 排除: 微钙化

## 为什么不猜置信度

模型没给出可能性数值时，``confidence`` 保持 ``None`` 并在界面上显示为"—"。
按下标反推一个数值（比如 0.8/0.6/0.4）会凭空制造出**看似精确的概率**，
医生会据此以为模型真的算过——宁可不给。

## 容错

先按约定格式解析；失败则退回宽松解析（"鉴别诊断：A、B、C"），此时只有
病种名与顺序，没有依据与数值（有顺序也比没有好）。全部失败返回空列表。
"""
from __future__ import annotations

import re
from collections.abc import Sequence

import structlog

from app.models.schemas import DifferentialDiagnosis

logger = structlog.get_logger("app.knowledge.differential")

# 候选病种数量上限：超过 5 个的鉴别清单在临床上没有可操作性，
# 而且会稀释排序信息（哪个最可能变得不重要）。
DEFAULT_MAX_CANDIDATES = 5

# "1. 名称 | 0.60 | 支持: a、b | 排除: c"（分隔符容忍 | / ｜ 与空白）
_STRUCTURED_RE = re.compile(
    r"^\s*(?P<rank>\d{1,2})\s*[.、)）]\s*"
    r"(?P<name>[^|｜]+)"
    r"(?:[|｜]\s*(?P<confidence>\d+(?:\.\d+)?|—|-|N/?A))?"
    r"(?:\s*[|｜]\s*支持\s*[:：]\s*(?P<support>[^|｜]*))?"
    r"(?:\s*[|｜]\s*排除\s*[:：]\s*(?P<exclude>[^|｜]*))?\s*$",
    re.IGNORECASE,
)

# "鉴别诊断：A、B、C" / "鉴别诊断: A、B、C"
_LIST_RE = re.compile(r"鉴别诊断\s*[:：]\s*(?P<body>[^\n]+)")

_LIST_SPLIT_RE = re.compile(r"[、,，;；/]")

# 病种编码（如 UDT-THYROID-PTC）夹在名称里时提取出来
_CODE_RE = re.compile(r"\b(UDT-[A-Z0-9\-]+)\b", re.IGNORECASE)


def _clean(text: str | None) -> str:
    return (text or "").strip().strip("。.;；,")


def _split_evidence(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item for item in (part.strip() for part in _LIST_SPLIT_RE.split(raw)) if item]


def _parse_confidence(raw: str | None) -> float | None:
    """解析可能性数值；缺失或占位符（—/N/A）返回 None。"""
    if not raw:
        return None
    text = raw.strip()
    if text in ("—", "-", "N/A", "n/a", "NA"):
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if value < 0.0 or value > 1.0:
        # 越界（例如模型按百分数给了 60）按百分数处理
        if 0.0 <= value <= 100.0:
            value = value / 100.0
        else:
            return None
    return round(value, 4)


def _parse_name(raw: str) -> tuple[str, str | None]:
    name = _clean(raw)
    code: str | None = None
    match = _CODE_RE.search(name)
    if match:
        code = match.group(1)
        name = _clean(name[: match.start()] + name[match.end():])
    return name, code


def parse_differential_diagnoses(
    text: str | None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> list[DifferentialDiagnosis]:
    """从 LLM 输出解析鉴别诊断清单（按 rank 升序，去重保序）。

    Args:
        text: LLM 输出全文。
        max_candidates: 最多返回多少个候选（默认 5）。

    Returns:
        :class:`DifferentialDiagnosis` 列表；无法解析时返回空列表。
    """
    if not text or not text.strip():
        return []

    items = _parse_structured(text)
    if not items:
        items = _parse_lenient(text)
    if not items:
        return []

    items.sort(key=lambda item: item.rank)
    return items[:max_candidates]


def _parse_structured(text: str) -> list[DifferentialDiagnosis]:
    items: list[DifferentialDiagnosis] = []
    seen: set = set()
    for line in text.splitlines():
        match = _STRUCTURED_RE.match(line)
        if not match:
            continue
        name, code = _parse_name(match.group("name"))
        if not name or name.lower() in seen:
            continue
        # 防止把"分级: TR4"这类非清单行误当候选
        if ":" in name or "：" in name:
            continue
        seen.add(name.lower())
        items.append(
            DifferentialDiagnosis(
                rank=int(match.group("rank")),
                disease_name=name,
                disease_code=code,
                confidence=_parse_confidence(match.group("confidence")),
                supporting_evidence=_split_evidence(match.group("support")),
                excluding_evidence=_split_evidence(match.group("exclude")),
            )
        )
    return items


def _parse_lenient(text: str) -> list[DifferentialDiagnosis]:
    """宽松解析：从"鉴别诊断：A、B、C"取病种名（无依据/无数值）。"""
    match = _LIST_RE.search(text)
    if not match:
        return []

    items: list[DifferentialDiagnosis] = []
    seen: set = set()
    rank = 0
    for raw in _LIST_SPLIT_RE.split(match.group("body")):
        name, code = _parse_name(raw)
        if not name or name.lower() in seen or len(name) > 40:
            continue
        seen.add(name.lower())
        rank += 1
        items.append(
            DifferentialDiagnosis(
                rank=rank,
                disease_name=name,
                disease_code=code,
            )
        )
    return items


def differential_summary(items: Sequence[DifferentialDiagnosis]) -> str:
    """生成一行摘要（用于 reasoning_trace 与日志）。"""
    return "、".join(item.disease_name for item in items)


__all__ = [
    "DEFAULT_MAX_CANDIDATES",
    "differential_summary",
    "parse_differential_diagnoses",
]
