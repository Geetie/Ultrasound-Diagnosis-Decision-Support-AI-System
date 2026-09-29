"""分级输出约束解码：JSON Schema（vLLM）与 GBNF（llama.cpp）双形态。

P0-2（升级规划 15 号）：把 A2 轮 prompt 级结构化输出经验固化为引擎级
schema 约束——两引擎在采样层被强制输出 `{"grading": "<枚举>"}`，
消除自由文本分级的表述分歧（真机 780 例 A2/A3 实测剩余 ~19.6% 不一致
主要来自该噪音）。

枚举与 server_build_eval_cases.py 的 expected_grading 严格对齐：
- thyroid: 2 / 3 / 4a / 4b / 5（TI-RADS）
- liver:   1 / 2 / 3 / 4 / 5（LI-RADS）
- cardiac: normal / mild / moderate / severe（LVEF ≥55/≥45/≥30）
"""
from __future__ import annotations

import json

GRADING_ENUMS: dict[str, list[str]] = {
    "thyroid": ["2", "3", "4a", "4b", "5"],
    "liver": ["1", "2", "3", "4", "5"],
    "cardiac": ["normal", "mild", "moderate", "severe"],
}


def grading_json_schema(organ: str) -> dict | None:
    """vLLM guided_json / structured_outputs 用的 JSON Schema。"""
    enum = GRADING_ENUMS.get(organ)
    if not enum:
        return None
    return {
        "type": "object",
        "properties": {
            "grading": {"type": "string", "enum": enum},
        },
        "required": ["grading"],
    }


def grading_gbnf(organ: str) -> str | None:
    """llama.cpp 语法约束用的 GBNF（枚举手写模板，避免通用转换器的复杂度）。"""
    enum = GRADING_ENUMS.get(organ)
    if not enum:
        return None
    alts = " | ".join(f'"{v}"' for v in enum)
    return (
        'root ::= "{" ws "\\"grading\\"" ws ":" ws "\\"grading_value\\"" ws "}" ws\n'
        f"grading_value ::= {alts}\n"
        'ws ::= " "*'
    )


def extract_guided_grading(text: str) -> str | None:
    """从约束输出中提取 grading 值（容错 markdown fence/前后缀文本）。

    返回 None 时调用方回退到 extract_grading_with_conflict（正则兜底）。
    """
    match = None
    for i, ch in enumerate(text):
        if ch in "{[":
            match = i
            break
    if match is None:
        return None
    end = max(text.rfind("}"), text.rfind("]"))
    if end <= match:
        return None
    try:
        data = json.loads(text[match : end + 1])
    except json.JSONDecodeError:
        # json_repair 为可选依赖（schema 单字段枚举，损坏概率极低；
        # 避免为此新增 requirements 依赖）
        try:
            import json_repair
        except ImportError:
            return None
        try:
            data = json_repair.loads(text[match : end + 1])
        except Exception:  # noqa: BLE001
            return None
    if isinstance(data, dict):
        grading = data.get("grading")
        if isinstance(grading, str) and grading.strip():
            return grading.strip()
    return None
