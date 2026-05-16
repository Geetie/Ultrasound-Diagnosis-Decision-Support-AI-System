import ctypes
import os
from typing import Any

import structlog

logger = structlog.get_logger()


class MemoryMonitor:
    """跨平台内存使用监控组件

    支持3种平台适配：psutil、/proc、Windows ctypes
    内存超预算时触发主动防御：拒绝新请求 + 触发GC + 告警升级

    单例模式（FIX-P2-03）：
    _over_budget改为实例变量，通过__new__确保全局唯一实例，
    避免多实例共享类变量导致的意外状态耦合。
    类方法保留为向后兼容接口，内部委托给单例实例。
    """

    _instance: "MemoryMonitor | None" = None

    def __new__(cls) -> "MemoryMonitor":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._over_budget = False
        return cls._instance

    @property
    def over_budget(self) -> bool:
        return self._over_budget

    def get_memory_usage_mb_instance(self) -> float:
        try:
            import psutil
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / (1024 * 1024)
        except ImportError:
            pass
        try:
            with open(f"/proc/{os.getpid()}/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024
        except Exception:
            pass
        try:
            class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                            ("PeakWorkingSetSize", ctypes.c_ulonglong), ("WorkingSetSize", ctypes.c_ulonglong),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_ulonglong), ("QuotaPagedPoolUsage", ctypes.c_ulonglong),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_ulonglong), ("QuotaNonPagedPoolUsage", ctypes.c_ulonglong),
                            ("PagefileUsage", ctypes.c_ulonglong), ("PeakPagefileUsage", ctypes.c_ulonglong),
                            ("PrivateUsage", ctypes.c_ulonglong)]
            counters = PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
            kernel32 = ctypes.windll.kernel32
            if kernel32.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return counters.WorkingSetSize / (1024 * 1024)
        except Exception:
            pass
        logger.warning("sg_memory_usage_unavailable")
        return -1.0

    def get_memory_status_instance(self, memory_mb: float, budget_mb: float) -> dict[str, Any]:
        if memory_mb < 0:
            status = "unknown"
        elif memory_mb <= budget_mb:
            status = "ok"
        else:
            status = "over_budget"
            if not self._over_budget:
                self._over_budget = True
                self._trigger_memory_defense(memory_mb, budget_mb)
        if status == "ok" and self._over_budget:
            self._over_budget = False
            logger.info("sg_memory_returned_to_budget")
        return {
            "rss_mb": round(memory_mb, 1),
            "budget_mb": budget_mb,
            "status": status,
        }

    def is_over_budget_instance(self) -> bool:
        return self._over_budget

    def _trigger_memory_defense(self, current_mb: float, budget_mb: float) -> None:
        logger.critical(
            "sg_memory_over_budget_triggering_defense",
            current_mb=round(current_mb, 1),
            budget_mb=budget_mb,
            action="gc_triggered+request_throttling",
            audit_event="safety_gateway_memory_over_budget",
        )
        import gc
        gc.collect()

    @classmethod
    def get_memory_usage_mb(cls) -> float:
        return cls().get_memory_usage_mb_instance()

    @classmethod
    def get_memory_status(cls, memory_mb: float, budget_mb: float) -> dict[str, Any]:
        return cls().get_memory_status_instance(memory_mb, budget_mb)

    @classmethod
    def is_over_budget(cls) -> bool:
        return cls().is_over_budget_instance()
