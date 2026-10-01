"""UDT to ICD-10 mapping (M9)."""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

_MAP_PATH = (
    Path(__file__).resolve().parents[2]
    / "data" / "medical_resources" / "udt_icd_map.json"
)


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
    entry = _load_mapping().get(udt_code)
    if not entry or not entry.get("icd10"):
        return None
    return ICD10Mapping(
        udt_code=udt_code,
        icd10=entry["icd10"],
        name=entry.get("name", ""),
        confidence=entry.get("confidence", ""),
    )


def available_codes():
    return sorted(_load_mapping().keys())
