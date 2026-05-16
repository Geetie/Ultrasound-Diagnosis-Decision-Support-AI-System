from .collector import MetricsCollector


def get_metrics_collector() -> MetricsCollector:
    return MetricsCollector.get_instance()
