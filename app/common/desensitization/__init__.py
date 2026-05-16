from app.common.desensitization.rule import DesensitizationRule
from app.common.desensitization.base import BaseDesensitizationEngine
from app.common.desensitization.recursive import (
    desensitize_dict,
    compile_patterns,
    validate_rules,
    MAX_RECURSION_DEPTH,
)

__all__ = [
    "DesensitizationRule",
    "BaseDesensitizationEngine",  # DEPRECATED: not used by any engine
    "desensitize_dict",
    "compile_patterns",
    "validate_rules",
    "MAX_RECURSION_DEPTH",
]
