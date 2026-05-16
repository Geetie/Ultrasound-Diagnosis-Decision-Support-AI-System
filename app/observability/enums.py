"""
可观测性日志框架的枚举定义。

本模块定义：
- LogCategory: 日志条目分类（诊断/运营/审计）
- NtpSyncStatus: NTP时间同步状态（IEC 62304合规）
- DeploymentProfile: 部署配置（compact/full）
"""

__all__ = ["LogCategory", "NtpSyncStatus", "DeploymentProfile", "AlertLevel"]

from enum import Enum


class LogCategory(str, Enum):
    DIAGNOSTIC = "diagnostic"
    OPERATIONAL = "operational"
    AUDIT = "audit"


class NtpSyncStatus(str, Enum):
    SYNCED = "synced"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"


class DeploymentProfile(str, Enum):
    COMPACT = "compact"
    FULL = "full"


class AlertLevel(Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
