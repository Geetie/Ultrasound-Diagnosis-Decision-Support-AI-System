"""零静默失败守卫（铁律：任何小步骤不许静默失败）。"""
from __future__ import annotations

import re
from pathlib import Path

GUARDED = [
    "app/services/diagnosis_pipeline.py",
    "app/services/diagnosis_worker.py",
    "app/services/safety_gateway_client.py",
    "app/fusion/layered_validator.py",
    "app/fusion/self_rag.py",
    "app/rag/retriever.py",
    "app/rag/service.py",
]

ROOT = Path(__file__).resolve().parents[3]


def _silent_catches(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8", errors="ignore")
    lines = src.splitlines()
    found: list[str] = []
    for i, line in enumerate(lines):
        if not re.search(r"except\s*(Exception|BaseException)?\s*:", line):
            continue
        body: list[str] = []
        j = i + 1
        while j < len(lines) and (
            lines[j].strip() == "" or lines[j].startswith((" ", ")", ":"))
        ):
            body.append(lines[j])
            j += 1
            if len(body) > 10:
                break
        text = "\n".join(body)
        has_log = any(
            k in text for k in ("logger.", "logging", "print(", "warnings.warn")
        )
        has_raise = "raise" in text
        has_err = ('\"error\"' in text) or ("error=" in text)
        if not has_log and not has_raise and not has_err:
            nxt = lines[i + 1].strip()[:50] if i + 1 < len(lines) else ""
            found.append(f"L{i + 1} {line.strip()[:60]} -> {nxt}")
    return found


def test_no_silent_catch_in_main_path():
    violations: list[str] = []
    for rel in GUARDED:
        p = ROOT / rel
        assert p.exists(), f"guarded file missing: {rel}"
        for v in _silent_catches(p):
            violations.append(f"{rel}: {v}")
    assert violations == [], (
        "main-path silent failures found:" + chr(10) + chr(10).join(violations)
    )
