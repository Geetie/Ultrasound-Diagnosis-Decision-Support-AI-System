
from app.config.settings import settings

from .monitor import CUSUMConfig, CUSUMMonitor


def create_cusum_monitor(
    target_rate: float | None = None,
    decision_threshold: float | None = None,
) -> CUSUMMonitor:
    """创建CUSUM监控器工厂函数

    从settings读取默认参数，支持显式覆盖。
    注意：使用is None判断而非or语义，避免0值被默认值覆盖。
    """
    config = CUSUMConfig(
        target_rate=target_rate if target_rate is not None else settings.obs_cusum_target_rate,
        decision_threshold=decision_threshold if decision_threshold is not None else settings.obs_cusum_decision_threshold,
    )
    return CUSUMMonitor(config)
