"""守卫（A.52）：**面向医生的错误文案必须是中文**（棘轮式，只允许收紧）。

背景（真机走查实录）：装载非 DICOM 文件时，医生看到的是后端英文原文
（`File too small` / `Missing required DICOM tags: PatientName`）—— 本轮把
医生可见面的错误文案全部改成中文，并加本守卫防止回退。

判据：`app/api/v1/*.py` 里 `error_message=` / `detail=` / `message=` 的字符串字面量，
若**含 ≥3 个连续 ASCII 字母且不含任何中文**，即视为「英文原文直给医生」→ 失败。

允许清单（机器对机器面，医生界面看不到，故显式豁免并写明原因）：
  - `device.py` —— 边缘设备代理/OTA/心跳，调用方是设备端程序
  - `alerts.py`  —— 告警 webhook（外部系统回调）
  - `utils.py`   —— 拼接错误码片段（consent_withdrawn 等），非自然语言
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
API_DIR = REPO_ROOT / "app" / "api" / "v1"

CJK = re.compile(r"[\u4e00-\u9fff]")
ASCII_WORD = re.compile(r"[A-Za-z]{3,}")
LITERAL = re.compile(r'(?:error_message|detail|message)\s*=\s*(?:f?"([^"]{4,160})")')

ALLOWLIST = {
    "device.py": "边缘设备代理/OTA/心跳：调用方是设备端程序，非医生界面",
    "alerts.py": "告警 webhook：外部系统回调，非医生界面",
    "utils.py": "错误码片段拼接（consent_withdrawn / consent_pending），非自然语言",
}


def _offenders(path: Path) -> list[str]:
    bad = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = LITERAL.search(line)
        if not m:
            continue
        msg = m.group(1)
        if CJK.search(msg):
            continue
        if ASCII_WORD.search(msg):
            bad.append(f"{path.name}:{lineno}: {msg}")
    return bad


def test_no_english_only_doctor_facing_messages():
    offenders: list[str] = []
    for path in sorted(API_DIR.glob("*.py")):
        if path.name in ALLOWLIST:
            continue
        offenders.extend(_offenders(path))
    assert offenders == [], (
        "医生可见面的错误文案出现英文原文（应写成中文，或加进 ALLOWLIST 并写明原因）：\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize("name", sorted(ALLOWLIST))
def test_allowlist_entries_still_exist(name: str):
    """允许清单不许留僵尸条目（文件改名/删除后必须同步）。"""
    assert (API_DIR / name).exists(), f"允许清单里的 {name} 已不存在，请清理"
