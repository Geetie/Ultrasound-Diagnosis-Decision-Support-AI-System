# DEPRECATED: This module is not used by any engine. See individual engine implementations.
from abc import ABC, abstractmethod
from typing import Any

import structlog
import yaml

from app.common.desensitization.recursive import (
    compile_patterns,
    desensitize_dict,
    validate_rules,
)
from app.common.desensitization.rule import DesensitizationRule

logger = structlog.get_logger()


class BaseDesensitizationEngine(ABC):
    """脱敏引擎抽象基类（计划中，尚未被任何引擎继承）

    共享框架：
    - 规则加载/编译/验证
    - 递归脱敏遍历（dict/list/tuple/set）
    - 规则管理（get/add/update/remove）

    子类需实现：
    - desensitize(): 单字符串脱敏
    - check_sensitive_data(): 敏感数据检测

    当前状态：DesensitizationEngine、FusionDesensitizationEngine、
    SafetyGatewayDesensitizationEngine 各自独立实现，未继承此类。
    主要障碍：单例模式冲突、DesensitizationRule 类型不一致、
    方法签名差异（如 Fusion 层使用 desensitize_llm_output 而非 desensitize）。
    待各引擎接口统一后再行重构。
    """

    def __init__(self) -> None:
        self._rules: list[DesensitizationRule] = []
        self._compiled_patterns: dict[str, Any] = {}

    def load_rules_from_yaml(self, rules_path: str) -> None:
        import os
        if not os.path.exists(rules_path):
            logger.warning("desensitization_rules_file_not_found", path=rules_path)
            return
        with open(rules_path, encoding="utf-8") as f:
            rules_data = yaml.safe_load(f)
        if rules_data and "rules" in rules_data:
            self._rules = [DesensitizationRule(**rule) for rule in rules_data["rules"]]
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        self._compiled_patterns = compile_patterns(self._rules)

    def _validate_rules(self) -> list[str]:
        return validate_rules(self._rules)

    def desensitize_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        return desensitize_dict(data, self.desensitize)

    def get_rules(self) -> list[DesensitizationRule]:
        return self._rules

    def add_rule(self, rule: DesensitizationRule) -> None:
        self._rules.append(rule)
        self._compile_patterns()

    def update_rule(self, rule_name: str, updated_rule: DesensitizationRule) -> None:
        for i, rule in enumerate(self._rules):
            if rule.name == rule_name:
                self._rules[i] = updated_rule
                break
        self._compile_patterns()

    def remove_rule(self, rule_name: str) -> None:
        self._rules = [r for r in self._rules if r.name != rule_name]
        self._compile_patterns()

    @abstractmethod
    def desensitize(self, text: str | None) -> str | None:
        ...

    @abstractmethod
    def check_sensitive_data(self, text: str) -> list[str]:
        ...
