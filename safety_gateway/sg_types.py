"""Safety Gateway类型定义模块（兼容层）

此模块已废弃，请直接从以下模块导入：
- ConsentStatus: from safety_gateway.validation_result import ConsentStatus
- SGError等异常别名: from safety_gateway.errors import ...

FIX-P2-05: 所有测试已迁移至直接导入，此模块将在下个版本移除。
"""

import warnings

warnings.warn(
    "safety_gateway.sg_types is deprecated. "
    "Import directly from safety_gateway.validation_result and safety_gateway.errors instead.",
    DeprecationWarning,
    stacklevel=2,
)

from safety_gateway.validation_result import ConsentStatus  # noqa: F401
from safety_gateway.errors import (  # noqa: F401
    SafetyGatewayError as SGError,
    ServiceUnavailableError as SGServiceUnavailableError,
    ConsentSafetyViolationError as SGConsentSafetyViolationError,
    DatabaseUnavailableError as SGDatabaseUnavailableError,
    CombinedUnavailableError as SGCombinedUnavailableError,
)
