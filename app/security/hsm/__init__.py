"""HSM 安全模块包。

提供硬件安全模块（HSM）的完整管理功能，包括密钥管理、加密操作、
降级状态管理和持久化。

架构（职责分离）：
    HSMManager (Facade)
    ├── DegradationManager   - 降级状态管理
    ├── TaskScheduler        - 定时任务调度
    ├── SoftHSMManager       - SoftHSM 生命周期
    ├── PersistenceManager   - Redis 持久化
    ├── KeyManager           - 密钥生命周期
    └── EncryptionService    - 加密操作

公共 API 完全兼容原始 app.security.hsm 模块。
"""

# 枚举和常量 - 保持向后兼容导出
from app.security.hsm.enums import (
    DegradedPhase,
    KeySlot,
    DegradedBehavior,
    DEGRADED_BEHAVIORS,
    get_key_slot,
)

# 子管理器 - 可选导出供高级使用
from app.security.hsm.degradation_manager import DegradationManager
from app.security.hsm.task_scheduler import TaskScheduler
from app.security.hsm.soft_hsm_manager import SoftHSMManager
from app.security.hsm.persistence_manager import PersistenceManager
from app.security.hsm.key_manager import KeyManager
from app.security.hsm.encryption_service import EncryptionService

# 门面管理器 - 主要入口
from app.security.hsm.manager import HSMManager

# 模块级单例 - 保持向后兼容
hsm_manager = HSMManager()

__all__ = [
    # 枚举和常量
    "DegradedPhase",
    "KeySlot",
    "DegradedBehavior",
    "DEGRADED_BEHAVIORS",
    "get_key_slot",
    # 子管理器
    "DegradationManager",
    "TaskScheduler",
    "SoftHSMManager",
    "PersistenceManager",
    "KeyManager",
    "EncryptionService",
    # 门面管理器
    "HSMManager",
    # 模块级单例
    "hsm_manager",
]
