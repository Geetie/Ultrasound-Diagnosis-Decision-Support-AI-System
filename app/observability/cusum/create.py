
from app.config.settings import settings

from .monitor import CUSUMConfig, CUSUMMonitor


def create_cusum_monitor(
    target_rate: float | None = None,
    decision_threshold: float | None = None,
) -> CUSUMMonitor:
    """创建CUSUM监控器工厂函数。

    A.114 / G18：默认参数不再直接读 settings，而是先走
    ``runtime_config.load_runtime_config()`` —— 校准产物（recommended_h /
    recommended_delta）存在且合法时采用它，缺失/非法时回退 settings 并留痕原因。
    显式传参仍优先（测试与一次性脚本用），与既有 `is None` 语义一致。
    """
    from .runtime_config import load_runtime_config

    config, source = load_runtime_config()
    if target_rate is not None:
        config = CUSUMConfig(
            target_rate=float(target_rate),
            decision_threshold=config.decision_threshold,
            delta=config.delta,
        )
    if decision_threshold is not None:
        config = CUSUMConfig(
            target_rate=config.target_rate,
            decision_threshold=float(decision_threshold),
            delta=config.delta,
        )
    monitor = CUSUMMonitor(config)
    monitor._config_source = source  # 运维对账：参数到底来自校准还是 settings
    return monitor
