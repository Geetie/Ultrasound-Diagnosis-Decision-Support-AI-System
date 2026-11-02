"""患者解释话术的**确定性**质量检查（A.138 / G39 部分）。

## 缺口

`patient_explanation`（M10）过去**零评估**：8 个测试文件全是解析与契约，没有任何一条
检查"这段话能不能给患者听"（G39）。用 LLM 当判官在这里不合适——判官本身要花钱、要模型、
会给"看起来合理"的分数；而下面这几条是**可以确定计算的**。

## 检查项（全部可离线计算，不依赖模型）

1. `not_chinese`：中文占比过低（话术是给中文患者看的）；
2. `phi_leak`：手机号 / 身份证 / 住院号 / `PATIENT_` 占位 / 示例 chunk id（`chunk_a1b2`）——
   这些是 A.70 事故里的原样外泄物；
3. `absolute_claim`：绝对化断言（确诊/一定能/百分之百/绝对/保证/无需复查）——
   辅助工具不得对患者下诊断承诺；
4. `placeholder`：模板占位符（`{...}` / `TODO` / `XXX`）；
5. `length`：`explanation` 过短（<8 字）或过长（>300 字）。

## 语义

- 返回 `evaluated=False` 表示"这次没有话术可评"（`None`），**不等于通过**；
- `passed=True` 只代表"上述确定性检查都过"，**不代表医学正确**——报告/界面文案必须保持这个边界。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.models.schemas import PatientExplanation

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_PHI_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mobile", re.compile(r"1[3-9]\d{9}")),
    ("id_card", re.compile(r"\b\d{17}[\dXx]\b")),
    ("patient_placeholder", re.compile(r"PATIENT_[A-Za-z0-9]+")),
    ("sample_chunk_id", re.compile(r"chunk_[a-z0-9]{4}")),
    ("admission_no", re.compile(r"(住院号|门诊号)\s*[:：]?\s*[A-Za-z0-9]{4,}")),
)
_ABSOLUTE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("confirmed_diagnosis", re.compile(r"确诊|明确诊断为|就是(?:癌|恶性|良性)")),
    ("guarantee", re.compile(r"百分之百|一定能|绝对(?:不会|是|没)|保证(?:不会|痊愈|治好)")),
    ("no_followup_needed", re.compile(r"无需(?:再)?复查|不用复查|以后不用管")),
)
_PLACEHOLDER_RE = re.compile(r"\{[^}]{1,20}\}|TODO|XXX+")

MIN_EXPLANATION_CHARS = 8
MAX_EXPLANATION_CHARS = 300
MIN_CJK_RATIO = 0.5


@dataclass
class QualityIssue:
    code: str
    field_name: str
    message: str


@dataclass
class ExplanationQuality:
    evaluated: bool = False
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool | None:
        """``None`` = 未评估（没有话术），不是"通过"。"""
        if not self.evaluated:
            return None
        return not self.issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "evaluated": self.evaluated,
            "passed": self.passed,
            "issues": [
                {"code": i.code, "field": i.field_name, "message": i.message}
                for i in self.issues
            ],
        }


def evaluate(explanation: PatientExplanation | None) -> ExplanationQuality:
    """对一段话术做确定性检查；``None`` → ``evaluated=False``。"""
    if explanation is None:
        return ExplanationQuality(evaluated=False)

    result = ExplanationQuality(evaluated=True)
    fields = {
        "explanation": explanation.explanation,
        "reassurance": explanation.reassurance,
        "next_step": explanation.next_step,
    }

    for name, value in fields.items():
        if not value:
            continue
        for code, pattern in _PHI_PATTERNS:
            if pattern.search(value):
                result.issues.append(
                    QualityIssue(code=f"phi_leak:{code}", field_name=name, message="疑似患者身份/占位信息外泄")
                )
        for code, pattern in _ABSOLUTE_PATTERNS:
            if pattern.search(value):
                result.issues.append(
                    QualityIssue(
                        code=f"absolute_claim:{code}",
                        field_name=name,
                        message="患者话术含绝对化/诊断承诺表述",
                    )
                )
        if _PLACEHOLDER_RE.search(value):
            result.issues.append(
                QualityIssue(code="placeholder", field_name=name, message="含模板占位符")
            )

    cjk = len(_CJK_RE.findall(explanation.explanation))
    ratio = cjk / max(1, len(explanation.explanation))
    if ratio < MIN_CJK_RATIO:
        result.issues.append(
            QualityIssue(code="not_chinese", field_name="explanation", message="中文占比过低")
        )

    length = len(explanation.explanation)
    if length < MIN_EXPLANATION_CHARS:
        result.issues.append(
            QualityIssue(code="length:too_short", field_name="explanation", message="话术过短")
        )
    elif length > MAX_EXPLANATION_CHARS:
        result.issues.append(
            QualityIssue(code="length:too_long", field_name="explanation", message="话术过长")
        )
    return result
