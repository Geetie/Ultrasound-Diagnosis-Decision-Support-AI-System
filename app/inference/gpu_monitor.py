"""
GPU和系统内存监控模块

提供：
- GPU内存监控与碎片化检测
- 系统内存测量（跨平台）
- GPU资源调度策略
"""

import asyncio
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

import structlog


class Platform(Enum):
    """支持的平台类型"""
    LINUX = "linux"
    WINDOWS = "windows"
    DARWIN = "darwin"
    UNKNOWN = "unknown"


def get_platform() -> Platform:
    """获取当前平台"""
    import sys
    platform_name = sys.platform
    if platform_name.startswith("linux"):
        return Platform.LINUX
    elif platform_name.startswith("win"):
        return Platform.WINDOWS
    elif platform_name == "darwin":
        return Platform.DARWIN
    return Platform.UNKNOWN


@dataclass
class GPUMemorySnapshot:
    """GPU内存快照"""
    timestamp: float
    total_memory_mb: float = 0.0
    used_memory_mb: float = 0.0
    free_memory_mb: float = 0.0
    fragmentation_rate: float = 0.0
    gpu_utilization_pct: float = 0.0
    gpu_temperature_c: float = 0.0
    gpu_name: str = ""

    @property
    def usage_pct(self) -> float:
        if self.total_memory_mb <= 0:
            return 0.0
        return self.used_memory_mb / self.total_memory_mb * 100


@dataclass
class SystemMemorySnapshot:
    """系统内存快照"""
    timestamp: float
    total_memory_mb: float = 0.0
    used_memory_mb: float = 0.0
    available_memory_mb: float = 0.0
    cached_mb: float = 0.0
    buffers_mb: float = 0.0
    process_used_mb: float = 0.0
    process_memory_mb: dict[str, float] = field(default_factory=dict)

    @property
    def usage_pct(self) -> float:
        if self.total_memory_mb <= 0:
            return 0.0
        return self.used_memory_mb / self.total_memory_mb * 100

    @property
    def effective_used_mb(self) -> float:
        """有效使用内存（减去缓存）"""
        return max(0.0, self.used_memory_mb - self.cached_mb - self.buffers_mb)


@dataclass
class FragmentationReport:
    """碎片化报告"""
    duration_hours: float = 0.0
    total_snapshots: int = 0
    max_fragmentation_rate: float = 0.0
    avg_fragmentation_rate: float = 0.0
    fragmentation_exceeded_threshold_count: int = 0
    warning_threshold: float = 0.30
    critical_threshold: float = 0.40
    snapshots: list[GPUMemorySnapshot] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.max_fragmentation_rate < self.critical_threshold


@dataclass
class MemoryProfileReport:
    """内存配置报告"""
    deployment_profile: str = "compact"
    total_system_memory_mb: float = 0.0
    measured_total_used_mb: float = 0.0
    measured_effective_used_mb: float = 0.0
    measured_available_mb: float = 0.0
    component_breakdown: dict[str, float] = field(default_factory=dict)
    budget_limit_mb: float = 22048.0
    snapshots: list[SystemMemorySnapshot] = field(default_factory=list)

    @property
    def within_budget(self) -> bool:
        # 如果有 measured_effective_used_mb 优先使用，否则回退到 measured_total_used_mb
        used_mb = self.measured_effective_used_mb if self.measured_effective_used_mb > 0 else self.measured_total_used_mb
        return used_mb <= self.budget_limit_mb


class FragmentationCalculator:
    """GPU碎片化计算器"""

    def __init__(self):
        self.using_pynvml = False
        self._pynvml_available = self._check_pynvml()
        self._log = structlog.get_logger("app.inference.fragmentation_calculator")

    def _check_pynvml(self) -> bool:
        """检查pynvml是否可用"""
        try:
            import pynvml
            pynvml.nvmlInit()
            pynvml.nvmlShutdown()
            return True
        except (ImportError, Exception):
            return False

    def calculate_fragmentation(
        self,
        total_mb: float,
        used_mb: float,
        free_mb: float,
        gpu_index: int = 0
    ) -> float:
        """
        计算碎片化率

        Args:
            total_mb: 总内存MB
            used_mb: 已使用内存MB
            free_mb: 空闲内存MB
            gpu_index: GPU索引

        Returns:
            float: 碎片化率 (0-1)
        """
        if self._pynvml_available:
            try:
                return self._calculate_with_pynvml(gpu_index)
            except Exception as e:
                self._log.warning("pynvml计算失败，使用fallback方法", error=str(e))

        return self._calculate_fallback(used_mb, total_mb, free_mb)

    def _calculate_with_pynvml(self, gpu_index: int) -> float:
        """使用pynvml计算GPU显存碎片化率

        一期方案：基于可用内存比例的改进估算。
        碎片化率 = 1 - (free_ratio / theoretical_free_ratio)
        当可用内存远小于理论可用内存时，碎片化率较高。
        二期应升级为基于最大连续可用块占比的精确计算。
        """
        import pynvml
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)

            total = mem_info.total
            used = mem_info.used
            free = mem_info.free

            if total <= 0:
                return 0.0

            free_ratio = free / total
            used_ratio = used / total
            theoretical_free_ratio = 1.0 - used_ratio

            if theoretical_free_ratio <= 0:
                return 0.0

            fragmentation = max(0.0, 1.0 - (free_ratio / theoretical_free_ratio))
            return min(fragmentation, 1.0)
        finally:
            pynvml.nvmlShutdown()

    def _calculate_fallback(self, used_mb: float, total_mb: float, free_mb: float) -> float:
        """无pynvml时的碎片化率估算

        一期方案：基于可用内存比例的估算。
        碎片化率 = max(0, 1 - free_mb / (total_mb - used_mb))
        当可用内存远小于理论可用内存时，碎片化率较高。
        """
        if total_mb <= 0:
            return 0.0
        theoretical_free_mb = total_mb - used_mb
        if theoretical_free_mb <= 0:
            return 0.0
        fragmentation = max(0.0, 1.0 - (free_mb / theoretical_free_mb))
        return min(fragmentation, 1.0)


class SystemMemoryReader(ABC):
    """系统内存读取接口"""

    @abstractmethod
    async def read_memory(self) -> SystemMemorySnapshot:
        pass


class LinuxMemoryReader(SystemMemoryReader):
    """Linux系统内存读取器"""

    async def read_memory(self) -> SystemMemorySnapshot:
        import shutil
        snapshot = SystemMemorySnapshot(timestamp=time.monotonic())

        try:
            if shutil.which("free"):
                proc = await asyncio.create_subprocess_exec(
                    "free", "-m",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
                lines = stdout.decode().strip().split("\n")
                if len(lines) >= 2:
                    parts = lines[1].split()
                    snapshot.total_memory_mb = float(parts[1])
                    snapshot.used_memory_mb = float(parts[2])
                    snapshot.available_memory_mb = float(parts[6]) if len(parts) > 6 else float(parts[3])
                    # 尝试获取缓存信息
                    if len(parts) > 5:
                        snapshot.cached_mb = float(parts[5])
                    if len(parts) > 4:
                        snapshot.buffers_mb = float(parts[4])
        except Exception as e:
            self._log_warning("system_memory_read_failed", str(e))

        return snapshot

    def _log_warning(self, event: str, error: str):
        import structlog
        structlog.get_logger("app.inference.linux_memory_reader").warning(event, error=error)


class WindowsMemoryReader(SystemMemoryReader):
    """Windows系统内存读取器"""

    async def read_memory(self) -> SystemMemorySnapshot:
        snapshot = SystemMemorySnapshot(timestamp=time.monotonic())
        try:
            import psutil
            vm = psutil.virtual_memory()
            snapshot.total_memory_mb = vm.total / (1024 * 1024)
            snapshot.used_memory_mb = vm.used / (1024 * 1024)
            snapshot.available_memory_mb = vm.available / (1024 * 1024)
            snapshot.cached_mb = getattr(vm, 'cached', 0) / (1024 * 1024)
            snapshot.buffers_mb = getattr(vm, 'buffers', 0) / (1024 * 1024)
        except ImportError:
            self._log_warning("psutil_not_available", "psutil未安装，使用空内存信息")
        except Exception as e:
            self._log_warning("system_memory_read_failed", str(e))

        return snapshot

    def _log_warning(self, event: str, error: str):
        import structlog
        structlog.get_logger("app.inference.windows_memory_reader").warning(event, error=error)


class DarwinMemoryReader(SystemMemoryReader):
    """macOS系统内存读取器"""

    async def read_memory(self) -> SystemMemorySnapshot:
        snapshot = SystemMemorySnapshot(timestamp=time.monotonic())
        try:
            import psutil
            vm = psutil.virtual_memory()
            snapshot.total_memory_mb = vm.total / (1024 * 1024)
            snapshot.used_memory_mb = vm.used / (1024 * 1024)
            snapshot.available_memory_mb = vm.available / (1024 * 1024)
        except ImportError:
            self._log_warning("psutil_not_available", "psutil未安装，使用空内存信息")
        except Exception as e:
            self._log_warning("system_memory_read_failed", str(e))

        return snapshot

    def _log_warning(self, event: str, error: str):
        import structlog
        structlog.get_logger("app.inference.darwin_memory_reader").warning(event, error=error)


def create_memory_reader(platform: Platform | None = None) -> SystemMemoryReader:
    """
    创建适合当前平台的内存读取器

    Args:
        platform: 平台类型，None表示自动检测

    Returns:
        SystemMemoryReader: 内存读取器实例
    """
    if platform is None:
        platform = get_platform()

    if platform == Platform.LINUX:
        return LinuxMemoryReader()
    elif platform == Platform.WINDOWS:
        return WindowsMemoryReader()
    elif platform == Platform.DARWIN:
        return DarwinMemoryReader()
    else:
        # 默认使用psutil
        try:
            import psutil
            if psutil:
                return WindowsMemoryReader()
        except ImportError:
            pass

        # fallback返回空实现
        class FallbackReader(SystemMemoryReader):
            async def read_memory(self) -> SystemMemorySnapshot:
                return SystemMemorySnapshot(timestamp=time.monotonic())
        return FallbackReader()


@dataclass
class ScheduledRequest:
    """调度请求"""
    resource_type: str
    request_id: str
    timestamp: float
    timeout_seconds: float
    callback: Callable[[bool], None] | None = None


class GPUScheduleStrategy:
    """GPU调度策略 - 互斥锁机制"""

    def __init__(
        self,
        llama_cpp_gpu_memory_mb: float = 3072.0,
        retrieval_gpu_budget_mb: float = 9216.0,
        total_gpu_memory_mb: float = 12288.0,
        default_timeout_seconds: float = 300.0
    ):
        self.llama_cpp_gpu_memory_mb = llama_cpp_gpu_memory_mb
        self.retrieval_gpu_budget_mb = retrieval_gpu_budget_mb
        self.total_gpu_memory_mb = total_gpu_memory_mb
        self.default_timeout_seconds = default_timeout_seconds

        self._llama_cpp_active = False
        self._retrieval_active = False
        self._llama_cpp_acquired_at: float | None = None
        self._retrieval_acquired_at: float | None = None

        self._pending_requests: deque[ScheduledRequest] = deque()
        self._timeout_check_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._llm_available = asyncio.Event()
        self._retrieval_available = asyncio.Event()
        self._log = structlog.get_logger("app.inference.gpu_schedule")

    @property
    def llama_cpp_active(self) -> bool:
        return self._llama_cpp_active

    @property
    def retrieval_active(self) -> bool:
        return self._retrieval_active

    async def acquire_llm(self) -> bool:
        """获取LLM资源"""
        async with self._lock:
            if self._retrieval_active:
                self._log.warning(
                    "llm_acquire_conflict",
                    msg="retrieval models using GPU, LLM must wait",
                )
                return False
            self._llama_cpp_active = True
            self._llama_cpp_acquired_at = time.monotonic()
            self._llm_available.set()
        self._log.info(
            "llm_gpu_acquired",
            gpu_mb=self.llama_cpp_gpu_memory_mb,
        )
        return True

    async def release_llm(self) -> None:
        """释放LLM资源"""
        async with self._lock:
            self._llama_cpp_active = False
            self._llama_cpp_acquired_at = None
            self._llm_available.clear()
        self._log.info(
            "llm_gpu_released",
            available_for_retrieval=self.retrieval_gpu_budget_mb,
        )
        self._process_pending_requests()

    async def acquire_retrieval(self) -> bool:
        """获取Retrieval资源"""
        async with self._lock:
            if self._llama_cpp_active:
                self._log.warning(
                    "retrieval_acquire_conflict",
                    msg="LLM using GPU, retrieval must wait",
                )
                return False
            self._retrieval_active = True
            self._retrieval_acquired_at = time.monotonic()
            self._retrieval_available.set()
        self._log.info("retrieval_gpu_acquired")
        return True

    async def release_retrieval(self) -> None:
        """释放Retrieval资源"""
        async with self._lock:
            self._retrieval_active = False
            self._retrieval_acquired_at = None
            self._retrieval_available.clear()
        self._log.info("retrieval_gpu_released")
        self._process_pending_requests()

    async def acquire_with_timeout(
        self,
        resource_type: str,
        timeout_seconds: float | None = None
    ) -> bool:
        """
        带超时的资源获取

        Args:
            resource_type: "llm" 或 "retrieval"
            timeout_seconds: 超时时间，None使用默认值

        Returns:
            bool: 是否成功获取
        """
        timeout = timeout_seconds or self.default_timeout_seconds

        if resource_type == "llm":
            if await self.acquire_llm():
                return True
        elif resource_type == "retrieval":
            if await self.acquire_retrieval():
                return True

        start_time = time.monotonic()
        self._log.info(
            "resource_waiting",
            resource_type=resource_type,
            timeout_seconds=timeout
        )

        while time.monotonic() - start_time < timeout:
            remaining = timeout - (time.monotonic() - start_time)
            try:
                if resource_type == "llm":
                    await asyncio.wait_for(self._llm_available.wait(), timeout=remaining)
                    async with self._lock:
                        if self._llama_cpp_active:
                            return True
                elif resource_type == "retrieval":
                    await asyncio.wait_for(self._retrieval_available.wait(), timeout=remaining)
                    async with self._lock:
                        if self._retrieval_active:
                            return True
            except asyncio.TimeoutError:
                break

        self._log.warning(
            "resource_acquire_timeout",
            resource_type=resource_type,
        )
        return False

    async def check_timeouts(self, max_hold_seconds: float | None = None) -> list[str]:
        """
        检查超时占用

        Args:
            max_hold_seconds: 最大占用时间，None使用默认超时的2倍

        Returns:
            List[str]: 超时释放的资源列表
        """
        max_hold = max_hold_seconds or (self.default_timeout_seconds * 2)
        now = time.monotonic()
        released = []

        if self._llama_cpp_active and self._llama_cpp_acquired_at:
            if now - self._llama_cpp_acquired_at > max_hold:
                self._log.warning(
                    "llm_hold_timeout",
                    held_seconds=now - self._llama_cpp_acquired_at
                )
                await self.release_llm()
                released.append("llm")

        if self._retrieval_active and self._retrieval_acquired_at:
            if now - self._retrieval_acquired_at > max_hold:
                self._log.warning(
                    "retrieval_hold_timeout",
                    held_seconds=now - self._retrieval_acquired_at
                )
                await self.release_retrieval()
                released.append("retrieval")

        return released

    def _remove_pending_request(self, request_id: str) -> None:
        """从等待队列移除请求"""
        self._pending_requests = deque(
            req for req in self._pending_requests
            if req.request_id != request_id
        )

    def _process_pending_requests(self) -> None:
        """处理等待队列中的请求"""
        # 简单的FIFO处理
        if self._pending_requests:
            next_req = self._pending_requests[0]
            self._log.info(
                "processing_pending_request",
                resource_type=next_req.resource_type,
                request_id=next_req.request_id
            )

    def get_schedule_state(self) -> dict:
        """获取调度状态"""
        now = time.monotonic()
        return {
            "llama_cpp_active": self._llama_cpp_active,
            "llama_cpp_held_seconds": (
                now - self._llama_cpp_acquired_at
                if self._llama_cpp_acquired_at else 0
            ),
            "retrieval_active": self._retrieval_active,
            "retrieval_held_seconds": (
                now - self._retrieval_acquired_at
                if self._retrieval_acquired_at else 0
            ),
            "llama_cpp_gpu_mb": self.llama_cpp_gpu_memory_mb,
            "retrieval_budget_mb": self.retrieval_gpu_budget_mb,
            "total_gpu_mb": self.total_gpu_memory_mb,
            "available_gpu_mb": self.total_gpu_memory_mb - (
                self.llama_cpp_gpu_memory_mb if self._llama_cpp_active else 0
            ) - (
                self.retrieval_gpu_budget_mb if self._retrieval_active else 0
            ),
            "pending_requests_count": len(self._pending_requests)
        }


class GPUMemoryMonitor:
    """GPU内存监控器"""

    def __init__(
        self,
        warning_threshold: float = 0.30,
        critical_threshold: float = 0.40,
        snapshot_interval_seconds: float = 60.0
    ):
        self._warning_threshold = warning_threshold
        self._critical_threshold = critical_threshold
        self._snapshot_interval = snapshot_interval_seconds
        self._max_snapshots = 1440
        self._log = structlog.get_logger("app.inference.gpu_monitor")
        self._snapshots: list[GPUMemorySnapshot] = []
        self._fragmentation_calculator = FragmentationCalculator()
        self._fragmentation_exceeded_since: float | None = None

    @classmethod
    def from_settings(cls, snapshot_interval_seconds: float = 60.0) -> "GPUMemoryMonitor":
        from app.config.settings import get_settings
        settings = get_settings()
        return cls(
            warning_threshold=settings.gpu_fragmentation_warning_threshold,
            critical_threshold=settings.gpu_fragmentation_critical_threshold,
            snapshot_interval_seconds=snapshot_interval_seconds,
        )

    async def take_snapshot(self) -> GPUMemorySnapshot:
        """获取GPU内存快照"""
        snapshot = await self._read_gpu_metrics()
        self._snapshots.append(snapshot)
        if len(self._snapshots) > self._max_snapshots:
            self._snapshots = self._snapshots[-self._max_snapshots:]

        if snapshot.fragmentation_rate > self._critical_threshold:
            self._log.critical(
                "gpu_fragmentation_critical",
                fragmentation=f"{snapshot.fragmentation_rate:.1%}",
                threshold=f"{self._critical_threshold:.1%}",
                used_mb=snapshot.used_memory_mb,
                total_mb=snapshot.total_memory_mb,
            )
        elif snapshot.fragmentation_rate > self._warning_threshold:
            self._log.warning(
                "gpu_fragmentation_warning",
                fragmentation=f"{snapshot.fragmentation_rate:.1%}",
                threshold=f"{self._warning_threshold:.1%}",
                used_mb=snapshot.used_memory_mb,
                total_mb=snapshot.total_memory_mb,
            )

        now = time.monotonic()
        if snapshot.fragmentation_rate > self._warning_threshold:
            if self._fragmentation_exceeded_since is None:
                self._fragmentation_exceeded_since = now
            duration = now - self._fragmentation_exceeded_since
            try:
                from app.observability.metrics_collector import get_metrics_collector
                get_metrics_collector().set_gpu_fragmentation_duration("gpu:0", duration)
            except Exception:
                pass
            if duration > 600:
                self._log.warning(
                    "gpu_fragmentation_sustained_warning",
                    fragmentation=f"{snapshot.fragmentation_rate:.1%}",
                    sustained_seconds=duration,
                    threshold_seconds=600,
                )
        else:
            self._fragmentation_exceeded_since = None
            try:
                from app.observability.metrics_collector import get_metrics_collector
                get_metrics_collector().set_gpu_fragmentation_duration("gpu:0", 0.0)
            except Exception:
                pass

        return snapshot

    async def run_continuous_monitor(
        self,
        duration_hours: float = 4.0
    ) -> FragmentationReport:
        """持续监控GPU"""
        start_time = time.monotonic()
        duration_seconds = duration_hours * 3600
        self._snapshots.clear()

        self._log.info(
            "gpu_monitoring_started",
            duration_hours=duration_hours,
            warning_threshold=f"{self._warning_threshold:.1%}",
            critical_threshold=f"{self._critical_threshold:.1%}",
            interval_seconds=self._snapshot_interval,
        )

        while (time.monotonic() - start_time) < duration_seconds:
            await self.take_snapshot()
            elapsed = time.monotonic() - start_time
            remaining = duration_seconds - elapsed
            if remaining > 0:
                sleep_time = min(self._snapshot_interval, remaining)
                await asyncio.sleep(sleep_time)

        return self._build_fragmentation_report(duration_hours)

    def _build_fragmentation_report(self, duration_hours: float) -> FragmentationReport:
        """构建碎片化报告"""
        report = FragmentationReport(
            duration_hours=duration_hours,
            total_snapshots=len(self._snapshots),
            warning_threshold=self._warning_threshold,
            critical_threshold=self._critical_threshold,
            snapshots=list(self._snapshots),
        )

        if not self._snapshots:
            return report

        frag_rates = [s.fragmentation_rate for s in self._snapshots]
        report.max_fragmentation_rate = max(frag_rates)
        report.avg_fragmentation_rate = sum(frag_rates) / len(frag_rates)
        report.fragmentation_exceeded_threshold_count = sum(
            1 for r in frag_rates if r > self._warning_threshold
        )

        return report

    async def _read_gpu_metrics(self) -> GPUMemorySnapshot:
        """读取GPU指标"""
        now = time.monotonic()
        snapshot = GPUMemorySnapshot(timestamp=now)

        try:
            import shutil
            if shutil.which("nvidia-smi"):
                proc = await asyncio.create_subprocess_exec(
                    "nvidia-smi",
                    "--query-gpu=memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,name",
                    "--format=csv,noheader,nounits",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
                lines = stdout.decode().strip().split("\n")
                if lines:
                    line = lines[0]
                    parts = [p.strip() for p in line.split(",")]

                    snapshot.total_memory_mb = float(parts[0])
                    snapshot.used_memory_mb = float(parts[1])
                    snapshot.free_memory_mb = float(parts[2])
                    snapshot.gpu_utilization_pct = float(parts[3])
                    snapshot.gpu_temperature_c = float(parts[4])
                    snapshot.gpu_name = parts[5] if len(parts) > 5 else ""

                    # 计算碎片化率
                    # 并发审查 P1-4：pynvml 不可用时该计算会回退到同步
                    # ``subprocess.run("nvidia-smi")``（30~150ms，异常时挂到
                    # 10s timeout）——同文件 :708 已用 create_subprocess_exec，
                    # 这里补一次 to_thread offload，避免阻塞监控协程。
                    snapshot.fragmentation_rate = await asyncio.to_thread(
                        self._fragmentation_calculator.calculate_fragmentation,
                        snapshot.total_memory_mb,
                        snapshot.used_memory_mb,
                        snapshot.free_memory_mb,
                    )
        except Exception as e:
            self._log.warning("gpu_metrics_read_failed", error=str(e))

        return snapshot


class SystemMemoryProfiler:
    """系统内存分析器"""

    def __init__(self, budget_limit_mb: float = 22048.0):
        self._budget_limit_mb = budget_limit_mb
        self._log = structlog.get_logger("app.inference.memory_profiler")
        self._snapshots: list[SystemMemorySnapshot] = []
        self._memory_reader = create_memory_reader()

    @classmethod
    def from_settings(cls) -> "SystemMemoryProfiler":
        from app.config.settings import get_settings
        settings = get_settings()
        return cls(budget_limit_mb=settings.compact_memory_budget_mb)

    async def take_snapshot(self) -> SystemMemorySnapshot:
        """获取内存快照"""
        snapshot = await self._memory_reader.read_memory()
        self._snapshots.append(snapshot)
        return snapshot

    async def profile_all_services(self) -> MemoryProfileReport:
        """分析所有服务"""
        snapshot = await self.take_snapshot()

        component_memory = await self._measure_docker_containers()

        report = MemoryProfileReport(
            deployment_profile="compact",
            total_system_memory_mb=snapshot.total_memory_mb,
            measured_total_used_mb=snapshot.used_memory_mb,
            measured_effective_used_mb=snapshot.effective_used_mb,
            measured_available_mb=snapshot.available_memory_mb,
            component_breakdown=component_memory,
            budget_limit_mb=self._budget_limit_mb,
            snapshots=list(self._snapshots),
        )

        self._log.info(
            "memory_profile_completed",
            total_used=f"{report.measured_total_used_mb:.0f}MB",
            effective_used=f"{report.measured_effective_used_mb:.0f}MB",
            available=f"{report.measured_available_mb:.0f}MB",
            budget=f"{report.budget_limit_mb:.0f}MB",
            within_budget=report.within_budget,
            components=component_memory,
        )

        return report

    async def _measure_docker_containers(self) -> dict[str, float]:
        """测量Docker容器内存使用"""
        containers: dict[str, float] = {}
        try:
            import shutil
            if shutil.which("docker"):
                proc = await asyncio.create_subprocess_exec(
                    "docker", "stats", "--no-stream", "--format",
                    "{{.Name}}\t{{.MemUsage}}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
                for line in stdout.decode().strip().split("\n"):
                    if not line.strip():
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        name = parts[0].strip()
                        mem_str = parts[1].strip()
                        mem_mb = self._parse_docker_mem(mem_str)
                        if mem_mb > 0:
                            containers[name] = mem_mb
        except Exception as e:
            self._log.warning("docker_stats_read_failed", error=str(e))

        return containers

    @staticmethod
    def _parse_docker_mem(mem_str: str) -> float:
        """解析Docker内存字符串"""
        mem_str = mem_str.split("/")[0].strip()
        try:
            if mem_str.endswith("TiB"):
                return float(mem_str[:-3]) * 1024 * 1024
            elif mem_str.endswith("GiB"):
                return float(mem_str[:-3]) * 1024
            elif mem_str.endswith("MiB"):
                return float(mem_str[:-3])
            elif mem_str.endswith("KiB"):
                return float(mem_str[:-3]) / 1024
            elif mem_str.endswith("TB"):
                return float(mem_str[:-2]) * 1024 * 1024
            elif mem_str.endswith("GB"):
                return float(mem_str[:-2]) * 1024
            elif mem_str.endswith("MB"):
                return float(mem_str[:-2])
            elif mem_str.endswith("KB"):
                return float(mem_str[:-2]) / 1024
            elif mem_str.endswith("B"):
                return float(mem_str[:-1]) / 1024 / 1024
        except ValueError:
            pass
        return 0.0


class GPUFragmentationMonitor:
    """GPU显存碎片化监控器"""

    def __init__(
        self,
        warning_threshold: float = 0.30,
        critical_threshold: float = 0.40,
        monitor_duration_seconds: float = 14400.0,
    ):
        self._warning_threshold = warning_threshold
        self._critical_threshold = critical_threshold
        self._monitor_duration_seconds = monitor_duration_seconds
        self._log = structlog.get_logger("app.inference.gpu_fragmentation_monitor")

    def compute_fragmentation_rate(self) -> float:
        try:
            import pynvml
            pynvml.nvmlInit()
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                total_free = mem_info.free
                total_allocated = mem_info.used
                total = mem_info.total
                if total <= 0:
                    return 0.0
                try:
                    bar1_info = pynvml.nvmlDeviceGetBAR1MemoryInfo(handle)
                    largest_free_block = bar1_info.bar1Free
                except (AttributeError, Exception):
                    largest_free_block = None
                if largest_free_block is not None and largest_free_block > 0:
                    return 1.0 - (largest_free_block / total_free) if total_free > 0 else 0.0
                return 1.0 - (total_free / (total_allocated + total_free)) if (total_allocated + total_free) > 0 else 0.0
            finally:
                pynvml.nvmlShutdown()
        except ImportError:
            self._log.debug("pynvml_not_available_using_nvidia_smi")
            return self._compute_fragmentation_via_smi()
        except Exception as e:
            self._log.warning("pynvml_fragmentation_failed", error=str(e))
            return self._compute_fragmentation_via_smi()

    def _compute_fragmentation_via_smi(self) -> float:
        import subprocess
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total,memory.used,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            if result.returncode != 0:
                return 0.0
            line = result.stdout.strip().split("\n")[0]
            parts = [p.strip() for p in line.split(",")]
            total_mb = float(parts[0])
            used_mb = float(parts[1])
            free_mb = float(parts[2])
            if total_mb <= 0:
                return 0.0
            return 1.0 - (free_mb / (used_mb + free_mb)) if (used_mb + free_mb) > 0 else 0.0
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError) as e:
            self._log.warning("nvidia_smi_fragmentation_failed", error=str(e))
            return 0.0

    def check_fragmentation(self) -> dict:
        rate = self.compute_fragmentation_rate()
        if rate >= self._critical_threshold:
            status = "critical"
        elif rate >= self._warning_threshold:
            status = "warning"
        else:
            status = "ok"
        return {
            "fragmentation_rate": rate,
            "status": status,
            "threshold_warning": self._warning_threshold,
            "threshold_critical": self._critical_threshold,
        }
