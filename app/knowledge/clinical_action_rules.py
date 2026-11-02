"""处置建议规则的**外部加载**（A.129 / G37）。

## 缺口

`clinical_actions.py` 的规则是模块常量（Python 字面量），现场想按本院流程改一条就必须
改代码、跑测试、发版；`feature_list.json` 的 `r14-rule-engine` 仍停在 planned（G37）。

## 设计（外部 YAML，严格校验，缺省回退）

- 文件路径由 `settings.clinical_actions_rules_path` 指定（**默认空 = 不启用**，
  行为与现在完全一致）；显式配置后才读文件。
- 结构：``rules:`` 列表，每条含 ``organ`` / ``action_type`` / 可选
  ``target_department`` / ``urgency`` / ``trigger_condition`` / ``description`` /
  ``applicable_level`` / ``source_reference`` / ``risk_levels``（命中任一即适用）/
  ``features_any``（命中任一特征即适用）。
- **严格校验**：organ 必须是受支持器官、urgency 必须是枚举值、``action_type`` 非空；
  任一条非法 → 整份文件拒绝（不让半份规则上线），并回落到内置常量 + 一条 error 日志。
  理由：处置建议直接影响临床行动，"部分加载成功"比"完全没加载"更危险。
- 触发语义：``risk_levels`` 与 ``features_any`` 都是**可选条件**；
  两个都空 = 无条件适用（该器官所有病例）。两者都有 = 满足任一即可（OR），
  与内置表"按风险等级分档 + 特征触发"的形态一致。
- 规则 ID：沿用 A.127 的口径 ``<organ>:<action_type>``（可用 ``rule_id`` 字段覆盖，
  便于版本化审计）。

## 与内置表的关系

外部规则**整体替换**内置表（不是合并）：合并会产生"这条到底是哪来的"歧义，
且顺序会漂。要回退内置表，把路径置空即可。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.config.settings import settings
from app.models.schemas import ClinicalAction, ClinicalActionUrgency, Organ

logger = structlog.get_logger("app.knowledge.clinical_action_rules")

_ALLOWED_ORGANS = {o.value for o in Organ}
_ALLOWED_URGENCIES = {u.value for u in ClinicalActionUrgency}


class ClinicalActionRule(BaseModel):
    """一条可外部维护的处置规则。"""

    organ: str
    action_type: str = Field(min_length=1, max_length=64)
    target_department: str | None = None
    urgency: ClinicalActionUrgency = ClinicalActionUrgency.ROUTINE
    trigger_condition: str | None = None
    description: str | None = None
    applicable_level: str | None = None
    source_reference: str | None = None
    risk_levels: list[str] = Field(default_factory=list)
    features_any: list[str] = Field(default_factory=list)
    rule_id: str | None = None

    @field_validator("organ")
    @classmethod
    def _organ_supported(cls, v: str) -> str:
        key = v.strip().lower()
        if key not in _ALLOWED_ORGANS:
            raise ValueError(f"不支持的组织/器官：{v}（允许：{sorted(_ALLOWED_ORGANS)}）")
        return key

    @field_validator("urgency", mode="before")
    @classmethod
    def _urgency_valid(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip().lower() not in _ALLOWED_URGENCIES:
            raise ValueError(f"非法紧急度：{v}（允许：{sorted(_ALLOWED_URGENCIES)}）")
        return str(v).strip().lower() if isinstance(v, str) else v

    @field_validator("risk_levels", mode="before")
    @classmethod
    def _levels_lower(cls, v: Any) -> Any:
        return [str(x).strip().lower() for x in v] if isinstance(v, list) else v


class ClinicalActionRulesFile(BaseModel):
    rules: list[ClinicalActionRule] = Field(default_factory=list)

    @field_validator("rules")
    @classmethod
    def _non_empty(cls, v: list[ClinicalActionRule]) -> list[ClinicalActionRule]:
        if not v:
            raise ValueError("rules 为空：要停用外部规则请把 settings.clinical_actions_rules_path 置空")
        return v


@lru_cache(maxsize=4)
def _load_cached(path: str, mtime: float) -> tuple[list[ClinicalActionRule], str | None]:
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        parsed = ClinicalActionRulesFile.model_validate(raw or {})
        return list(parsed.rules), None
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        detail = str(exc)[:400]
        logger.error("clinical_action_rules_load_failed", path=path, error=detail)
        return [], detail


def load_rules(path: str | None = None) -> tuple[list[ClinicalActionRule], str | None]:
    """加载外部规则；返回 (规则列表, 错误描述)。**失败返回空列表**（调用方回退内置表）。"""
    resolved = path or getattr(settings, "clinical_actions_rules_path", "") or ""
    resolved = str(resolved).strip()
    if not resolved:
        return [], None
    if not os.path.exists(resolved):
        logger.warning("clinical_action_rules_missing", path=resolved)
        return [], f"file_not_found:{resolved}"
    try:
        mtime = os.path.getmtime(resolved)
    except OSError:
        mtime = 0.0
    return _load_cached(resolved, mtime)


def rule_applies(rule: ClinicalActionRule, features: Sequence[str], risk_level: str | None) -> bool:
    """判定规则是否适用（risk_levels / features_any 任一为空则该项不限制；两者都有取 OR）。"""
    level = getattr(risk_level, "value", risk_level)
    level = str(level).strip().lower() if level is not None else None
    feature_set = {str(f).strip().lower() for f in features}

    by_level = bool(level and level in set(rule.risk_levels)) if rule.risk_levels else None
    by_feature = bool(feature_set & set(rule.features_any)) if rule.features_any else None

    if by_level is None and by_feature is None:
        return True  # 无条件规则
    return bool(by_level) or bool(by_feature)


def actions_from_rules(
    rules: Sequence[ClinicalActionRule],
    *,
    organ: str,
    features: Sequence[str],
    risk_level: str | None,
) -> list[ClinicalAction]:
    """按规则表生成处置建议（与内置表同形的 ClinicalAction）。"""
    organ_key = organ.strip().lower()
    out: list[ClinicalAction] = []
    for rule in rules:
        if rule.organ != organ_key:
            continue
        if not rule_applies(rule, features, risk_level):
            continue
        out.append(
            ClinicalAction(
                action_type=rule.action_type,
                rule_id=rule.rule_id or f"{organ_key}:{rule.action_type}",
                target_department=rule.target_department,
                urgency=rule.urgency,
                trigger_condition=rule.trigger_condition,
                description=rule.description,
                applicable_level=rule.applicable_level,
                source_reference=rule.source_reference,
            )
        )
    return out
