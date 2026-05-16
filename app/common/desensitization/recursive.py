import re
from typing import Any

import structlog

from app.common.desensitization.rule import DesensitizationRule

logger = structlog.get_logger()

MAX_RECURSION_DEPTH = 20


def desensitize_dict(data: dict[str, Any], desensitize_fn, _depth: int = 0) -> dict[str, Any]:
    if _depth >= MAX_RECURSION_DEPTH:
        logger.error("desensitize_dict_recursion_depth_exceeded", depth=_depth)
        raise ValueError(f"Desensitization recursion depth exceeded (max={MAX_RECURSION_DEPTH}). Data at depth {_depth} was not desensitized.")
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = desensitize_fn(value)
        elif isinstance(value, dict):
            result[key] = desensitize_dict(value, desensitize_fn, _depth + 1)
        elif isinstance(value, list):
            result[key] = _desensitize_list(value, desensitize_fn, _depth + 1)
        elif isinstance(value, tuple):
            result[key] = tuple(
                desensitize_fn(item) if isinstance(item, str) else
                desensitize_dict(item, desensitize_fn, _depth + 1) if isinstance(item, dict) else
                item
                for item in value
            )
        elif isinstance(value, set):
            result[key] = {
                desensitize_fn(item) if isinstance(item, str) else item
                for item in value
            }
        else:
            result[key] = value
    return result


def _desensitize_list(data: list[Any], desensitize_fn, _depth: int = 0) -> list[Any]:
    if _depth >= MAX_RECURSION_DEPTH:
        logger.error("desensitize_list_recursion_depth_exceeded", depth=_depth)
        raise ValueError(f"Desensitization recursion depth exceeded (max={MAX_RECURSION_DEPTH}). Data at depth {_depth} was not desensitized.")
    result = []
    for item in data:
        if isinstance(item, str):
            result.append(desensitize_fn(item))
        elif isinstance(item, dict):
            result.append(desensitize_dict(item, desensitize_fn, _depth + 1))
        elif isinstance(item, list):
            result.append(_desensitize_list(item, desensitize_fn, _depth + 1))
        else:
            result.append(item)
    return result


def compile_patterns(rules: list[DesensitizationRule]) -> dict[str, re.Pattern]:
    compiled = {}
    for rule in rules:
        if rule.enabled:
            try:
                compiled[rule.name] = re.compile(rule.pattern)
            except re.error as e:
                logger.warning(
                    "desensitization_rule_compile_failed",
                    rule_name=rule.name,
                    pattern=rule.pattern,
                    error=str(e),
                )
    return compiled


def validate_rules(rules: list[DesensitizationRule]) -> list[str]:
    errors = []
    names = set()
    for rule in rules:
        if not rule.name:
            errors.append("规则名称不能为空")
        if rule.name in names:
            errors.append(f"规则名称重复: {rule.name}")
        names.add(rule.name)
        try:
            re.compile(rule.pattern)
        except re.error as e:
            errors.append(f"规则'{rule.name}'正则表达式无效: {e}")
    return errors
