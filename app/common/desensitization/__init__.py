from app.common.desensitization.base import BaseDesensitizationEngine
from app.common.desensitization.recursive import (
    MAX_RECURSION_DEPTH,
    compile_patterns,
    desensitize_dict,
    validate_rules,
)
from app.common.desensitization.rule import DesensitizationRule

__all__ = [
    "MAX_RECURSION_DEPTH",
    "BaseDesensitizationEngine",  # DEPRECATED: not used by any engine
    "DesensitizationRule",
    "compile_patterns",
    "desensitize_dict",
    "validate_rules",
]
