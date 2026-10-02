"""UDT to ICD-10 mapping (M9).

M9 统计口径前置：诊断结果的 UDT 病种编码（如 thyroid_hyperthyroidism）
→ ICD-10 临床版 2.0 标准编码（统计上报用）。映射表由
scripts/build_udt_icd_map.py 生成（35,862 条底表检索 + 医学人工补录）。

失败安全：映射表缺失 / UDT 码未收录 / confidence 为
manual_pending_review 时行为见各函数 docstring——不编造编码。
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

_MAP_PATH = (
    Path(__file__).resolve().parents[2]
    / "data" / "medical_resources" / "udt_icd_map.json"
)

# 待医学信息学复核的置信级别：这些映射可用但不建议直接进正式上报
_PENDING = "manual_pending_review"


class ICD10Mapping(BaseModel):
    udt_code: str
    icd10: str
    name: str
    confidence: str


@lru_cache(maxsize=1)
def _load_mapping():
    if not _MAP_PATH.exists():
        return {}
    data = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
    return data.get("mapping", {})


def udt_to_icd10(udt_code: str):
    """UDT 码 → ICD-10 映射。未收录返回 None（不编造编码）。"""
    entry = _load_mapping().get(udt_code)
    if not entry or not entry.get("icd10"):
        return None
    return ICD10Mapping(
        udt_code=udt_code,
        icd10=entry["icd10"],
        name=entry.get("name", ""),
        confidence=entry.get("confidence", ""),
    )


def udt_to_icd10_reported(udt_code: str):
    """仅返回可进入正式统计上报的映射（排除 manual_pending_review）。

    M9 口径：pending 的映射需要医学信息学复核后才能用于上报，
    观测/内部统计可用 :func:`udt_to_icd10`。
    """
    m = udt_to_icd10(udt_code)
    if m is None or m.confidence == _PENDING:
        return None
    return m


def codes_by_organ(organ: str):
    """按器官过滤已映射的 UDT 码（organ 前缀匹配，如 'thyroid'）。"""
    prefix = f"{organ}_"
    return [c for c in available_codes() if c.startswith(prefix)]


def available_codes():
    return sorted(_load_mapping().keys())
