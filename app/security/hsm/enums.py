"""HSM 枚举类型和常量定义。

将 HSM 相关的枚举、数据类和常量从 HSMManager 中提取出来，
作为独立模块供各子管理器共享使用。
"""

from typing import Optional, Dict
from enum import Enum
from dataclasses import dataclass


class DegradedPhase(str, Enum):
    """HSM 降级阶段枚举。"""
    NORMAL = "normal"
    CACHED = "cached"
    RESTRICTED = "restricted"
    EMERGENCY = "emergency"


class KeySlot(str, Enum):
    """密钥槽位枚举。"""
    JWT_SIGNING = "jwt_signing"
    DATA_ENCRYPTION = "data_encryption"
    DESENSITIZATION_MAPPING = "desensitization_mapping"
    EMERGENCY_SIGNING = "emergency_signing"


@dataclass
class DegradedBehavior:
    """降级行为配置，定义各阶段允许的操作。"""
    allow_jwt_signing: bool
    allow_data_encryption: bool
    allow_desensitization_restore: bool
    allow_emergency_signing: bool
    max_signature_count: Optional[int] = None


DEGRADED_BEHAVIORS: Dict[DegradedPhase, DegradedBehavior] = {
    DegradedPhase.NORMAL: DegradedBehavior(
        allow_jwt_signing=True,
        allow_data_encryption=True,
        allow_desensitization_restore=True,
        allow_emergency_signing=False,
    ),
    DegradedPhase.CACHED: DegradedBehavior(
        allow_jwt_signing=True,
        allow_data_encryption=True,
        allow_desensitization_restore=True,
        allow_emergency_signing=False,
    ),
    DegradedPhase.RESTRICTED: DegradedBehavior(
        allow_jwt_signing=False,
        allow_data_encryption=True,
        allow_desensitization_restore=True,
        allow_emergency_signing=False,
    ),
    DegradedPhase.EMERGENCY: DegradedBehavior(
        allow_jwt_signing=False,
        allow_data_encryption=False,
        allow_desensitization_restore=False,
        allow_emergency_signing=True,
        max_signature_count=50,
    ),
}

_KEY_SLOT_ROUTING = {
    "jwt_": KeySlot.JWT_SIGNING,
    "desens_": KeySlot.DESENSITIZATION_MAPPING,
    "emergency_": KeySlot.EMERGENCY_SIGNING,
}


def get_key_slot(key_name: str) -> KeySlot:
    """根据密钥名称前缀路由到对应的密钥槽位。

    Args:
        key_name: 密钥名称

    Returns:
        KeySlot: 对应的密钥槽位
    """
    for prefix, slot in _KEY_SLOT_ROUTING.items():
        if key_name.startswith(prefix):
            return slot
    return KeySlot.DATA_ENCRYPTION
