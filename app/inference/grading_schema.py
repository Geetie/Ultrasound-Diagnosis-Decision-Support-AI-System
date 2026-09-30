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
    """llama.cpp 语法约束用的 GBNF（枚举手写模板，避免通用转换器的复杂度）。

    ⚠️ GBNF 三个坑（真机 2026-09-30 实测）：
    1. 规则名字符集是 [a-zA-Z0-9-]——**不含下划线**（`grading_value` 会被
       解析器在 `grading` 处截断报 "expecting ::="），规则名用连字符；
    2. root 里引用子规则**不带引号**（带引号 = 字面量字符串，会输出
       `{"grading": "grading-value"}` 这种字面值）；
    3. 字符串字面量的引号**不输出**——枚举分支 `"3"` 生成裸 `3`（int 而非
       str），值必须显式包引号：`"\\" ("2"|...) "\\""`。
    """
    enum = GRADING_ENUMS.get(organ)
    if not enum:
        return None
    alts = " | ".join(f'"{v}"' for v in enum)
    # 裸值模板（root ::= "2" | "3" | ...）：绕开 GBNF 字符串转义/引号的
    # 全部坑（llama-cpp-python 0.3.35 上 `\"` 转义与 chat+grammar 组合
    # 会段错误，真机 2026-09-30 实测）——输出就是裸枚举值，白名单抽取。
    return f"root ::= {alts}\n"


def extract_guided_grading(text: str, organ: str | None = None) -> str | None:
    """从约束输出中提取 grading 值。

    三级：①裸值白名单（GBNF 裸值模板的输出，去空白后精确匹配枚举）
    ②JSON 路径（vLLM guided_json 输出，容错 fence/前后缀）
    ③都失败返回 None，调用方回退 extract_grading_with_conflict 正则兜底。
    """
    stripped = text.strip()
    enum = GRADING_ENUMS.get(organ) if organ else None
    if enum and stripped in enum:
        return stripped
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
        # GBNF 值分支可能产出裸数字（int）而非字符串（字面量引号坑），
        # 统一 str 化
        if grading is not None and str(grading).strip():
            return str(grading).strip()
    return None
