def compute_percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (p / 100.0) * (len(sorted_data) - 1)
    lower = int(idx)
    upper = lower + 1
    if upper >= len(sorted_data):
        return sorted_data[-1]
    weight = idx - lower
    return sorted_data[lower] * (1 - weight) + sorted_data[upper] * weight


def print_benchmark_report(test_name: str, results: dict):
    print(f"\n{'=' * 60}")
    print(f"  Benchmark Report: {test_name}")
    print(f"{'=' * 60}")
    for metric, value in results.items():
        if isinstance(value, float):
            print(f"  {metric:30s} {value:12.3f} ms")
        else:
            print(f"  {metric:30s} {value}")
    print(f"{'=' * 60}\n")
