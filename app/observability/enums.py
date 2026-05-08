"""
Enumerations for the observability logging framework.

This module defines:
- LogCategory: Classification of log entries (diagnostic/operational/audit)
- NtpSyncStatus: NTP time synchronization status for IEC 62304 compliance
- DeploymentProfile: Deployment configuration (compact/full)
"""

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
