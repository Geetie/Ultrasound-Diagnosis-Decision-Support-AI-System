"""M10 患者解释话术解析。

## 定位

患者解释话术是**给患者听的**，不是给医生看的结论。因此：

1. **默认不进入正式报告**：报告是医疗文书，混入面向患者的安抚性表述会改变
   文书性质；由医生按需展示或单独打印。
2. **不生成诊断性承诺**：解析只做"取模型给出的话术"，不推断、不补全。模型
   没给就没有（``None``），界面据此不渲染——绝不拼凑一句看似合理的话术。

约定格式（提示词中要求）：

    患者解释:
    解释: 结节多数为良性，本次超声未见明显恶性征象
    安抚: 不必过度担心，规律复查即可
    下一步: 建议 6 个月后复查超声

也兼容单行写法（"患者解释: 结节多数为良性…"），此时只有 ``explanation``。
"""
from __future__ import annotations

import re
from typing import Optional

import structlog

from app.models.schemas import PatientExplanation

logger = structlog.get_logger("app.knowledge.patient_explanation")

# 字段一律**行首锚定**且分隔空白不含换行：否则 "患者解释:"（块头）后面的
# 换行会被 \s* 吃掉，把下一行的 "解释: xxx" 整段当成块头的值，
# 解析结果会多出 "解释: " 前缀。
_FIELD_PATTERNS = {
    "explanation": re.compile(r"^[ \t]*解释[ \t]*[:：][ \t]*(?P<value>[^\n]+)", re.MULTILINE),
    "reassurance": re.compile(r"^[ \t]*安抚[ \t]*[:：][ \t]*(?P<value>[^\n]+)", re.MULTILINE),
    "next_step": re.compile(r"^[ \t]*下一步[ \t]*[:：][ \t]*(?P<value>[^\n]+)", re.MULTILINE),
}
_INLINE_RE = re.compile(
    r"^[ \t]*患者(?:解释|沟通|说明)[ \t]*[:：][ \t]*(?P<value>[^\n]+)", re.MULTILINE
)


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip().strip("。.;；,")
    return text or None


def parse_patient_explanation(text: Optional[str]) -> Optional[PatientExplanation]:
    """从 LLM 输出解析患者解释话术。

    Returns:
        :class:`PatientExplanation`；未给出话术时返回 ``None``（不编造）。
    """
    if not text or not text.strip():
        return None

    fields: dict = {}
    for key, pattern in _FIELD_PATTERNS.items():
        match = pattern.search(text)
        if match:
            fields[key] = _clean(match.group("value"))

    explanation = fields.get("explanation")
    if not explanation:
        # 单行写法（"患者解释: xxx"）：块头正则要求冒号后为空，因此这里单独兜底
        inline = _INLINE_RE.search(text)
        if inline:
            explanation = _clean(inline.group("value"))

    if not explanation:
        return None

    return PatientExplanation(
        explanation=explanation,
        reassurance=fields.get("reassurance"),
        next_step=fields.get("next_step"),
        source_reference="模型生成（需医生确认后方可告知患者）",
    )


__all__ = ["parse_patient_explanation"]
