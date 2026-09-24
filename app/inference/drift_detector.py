
"""
模型漂移检测器模块

提供模型性能漂移检测功能，包括：
- 性能基线记录与管理
- 统计过程控制(SPC)
- 漂移警报机制
- 漂移报告生成
"""

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("app.inference.drift_detector")


class DriftSeverity(str, Enum):
    """漂移严重程度"""
    NONE = "none"
    WARNING = "warning"
    CRITICAL = "critical"


class MetricType(str, Enum):
    """监控指标类型"""
    ACCURACY = "accuracy"
    LATENCY_P50 = "latency_p50"
    LATENCY_P99 = "latency_p99"
    HALLUCINATION_RATE = "hallucination_rate"
    GRADING_CONSISTENCY = "grading_consistency"


@dataclass
class MetricSnapshot:
    """指标快照"""
    timestamp: str
    metric_type: MetricType
    value: float
    organ: str = "all"
    model_version: str = ""


@dataclass
class PerformanceBaseline:
    """性能基线"""
    metric_type: MetricType
    mean: float
    std_dev: float
    sample_size: int
    warning_threshold: float  # 2 sigma
    critical_threshold: float  # 3 sigma
    organ: str = "all"
    created_at: str = ""
    updated_at: str = ""


@dataclass
class DriftAlert:
    """漂移警报"""
    severity: DriftSeverity
    metric_type: MetricType
    current_value: float
    baseline_mean: float
    deviation: float  # 偏离值（绝对值）
    deviation_pct: float  # 偏离百分比
    organ: str = "all"
    timestamp: str = ""
    message: str = ""


@dataclass
class DriftReport:
    """漂移检测报告"""
    model_name: str
    model_version: str
    test_timestamp: str
    alerts: list[DriftAlert] = field(default_factory=list)
    snapshots: list[MetricSnapshot] = field(default_factory=list)
    baselines: dict[MetricType, PerformanceBaseline] = field(default_factory=dict)
    overall_status: DriftSeverity = DriftSeverity.NONE
    test_data_version: str = ""


class DriftDetector:
    """模型漂移检测器"""

    def __init__(
        self,
        model_name: str,
        model_version: str = "",
        baseline_dir: str | None = None,
    ):
        """
        初始化漂移检测器

        Args:
            model_name: 模型名称
            model_version: 模型版本
            baseline_dir: 基线数据存储目录
        """
        self.model_name = model_name
        self.model_version = model_version
        self.baseline_dir = Path(baseline_dir) if baseline_dir else Path("data/drift_baselines")
        self.baseline_dir.mkdir(parents=True, exist_ok=True)
        self.baselines: dict[str, dict[MetricType, PerformanceBaseline]] = {}

        logger.info(
            "drift_detector_initialized",
            model_name=model_name,
            model_version=model_version,
            baseline_dir=str(self.baseline_dir),
        )

    def _get_baseline_path(self, organ: str = "all", metric_type: str | None = None) -> Path:
        """获取基线文件路径"""
        safe_model_name = self.model_name.replace("/", "_").replace("\\", "_")
        if metric_type:
            return self.baseline_dir / f"{safe_model_name}_{organ}_{metric_type}.json"
        return self.baseline_dir / f"{safe_model_name}_{organ}_baseline.json"

    def save_baseline(self, baseline: PerformanceBaseline, organ: str = "all") -> bool:
        """
        保存性能基线

        Args:
            baseline: 性能基线
            organ: 器官类型

        Returns:
            bool: 是否保存成功
        """
        try:
            if organ not in self.baselines:
                self.baselines[organ] = {}
            self.baselines[organ][baseline.metric_type] = baseline

            baseline_data = {
                "model_name": self.model_name,
                "model_version": self.model_version,
                "organ": organ,
                "metric_type": baseline.metric_type.value,
                "mean": baseline.mean,
                "std_dev": baseline.std_dev,
                "sample_size": baseline.sample_size,
                "warning_threshold": baseline.warning_threshold,
                "critical_threshold": baseline.critical_threshold,
                "created_at": baseline.created_at or datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }

            file_path = self._get_baseline_path(organ, baseline.metric_type.value)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(baseline_data, f, ensure_ascii=False, indent=2)

            logger.info(
                "baseline_saved",
                model_name=self.model_name,
                metric_type=baseline.metric_type.value,
                organ=organ,
            )
            return True
        except FileNotFoundError as e:
            logger.warning("baseline_dir_not_found", path=str(e))
            return False
        except (OSError, PermissionError) as e:
            logger.error("baseline_save_io_error", error=str(e))
            return False
        except Exception as e:
            logger.error("baseline_save_failed", model_name=self.model_name, error=str(e))
            return False

    def load_baseline(self, metric_type: MetricType, organ: str = "all") -> PerformanceBaseline | None:
        """
        加载性能基线

        Args:
            metric_type: 指标类型
            organ: 器官类型

        Returns:
            Optional[PerformanceBaseline]: 性能基线，如果不存在则返回None
        """
        if organ in self.baselines and metric_type in self.baselines[organ]:
            return self.baselines[organ][metric_type]

        try:
            file_path = self._get_baseline_path(organ, metric_type.value)
            if not file_path.exists():
                return None

            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)

            baseline = PerformanceBaseline(
                metric_type=MetricType(data["metric_type"]),
                mean=data["mean"],
                std_dev=data["std_dev"],
                sample_size=data["sample_size"],
                warning_threshold=data["warning_threshold"],
                critical_threshold=data["critical_threshold"],
                organ=data.get("organ", "all"),
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
            )

            if organ not in self.baselines:
                self.baselines[organ] = {}
            self.baselines[organ][metric_type] = baseline

            logger.info(
                "baseline_loaded",
                model_name=self.model_name,
                metric_type=metric_type.value,
                organ=organ,
            )
            return baseline
        except FileNotFoundError:
            logger.info("baseline_file_not_found", model_name=self.model_name, metric_type=metric_type.value, organ=organ)
            return None
        except json.JSONDecodeError as e:
            logger.error("baseline_file_corrupted", model_name=self.model_name, error=str(e))
            return None
        except Exception as e:
            logger.error("baseline_load_failed", model_name=self.model_name, metric_type=metric_type.value, error=str(e))
            return None

    def compute_baseline(
        self,
        metric_type: MetricType,
        historical_values: list[float],
        organ: str = "all",
    ) -> PerformanceBaseline | None:
        """
        从历史数据计算性能基线

        Args:
            metric_type: 指标类型
            historical_values: 历史值列表
            organ: 器官类型

        Returns:
            Optional[PerformanceBaseline]: 计算得到的性能基线
        """
        min_samples = 10 if metric_type in [
            MetricType.ACCURACY, MetricType.GRADING_CONSISTENCY, MetricType.HALLUCINATION_RATE
        ] else 30
        if len(historical_values) < min_samples:
            logger.warning(
                "insufficient_data_for_baseline",
                model_name=self.model_name,
                metric_type=metric_type.value,
                sample_size=len(historical_values),
                required=min_samples,
            )
            return None

        mean = sum(historical_values) / len(historical_values)
        variance = sum((x - mean) ** 2 for x in historical_values) / max(len(historical_values) - 1, 1)
        std_dev = math.sqrt(variance)

        if metric_type == MetricType.HALLUCINATION_RATE:
            from app.inference.verification_config import get_verification_config
            thresholds = get_verification_config().consistency_thresholds
            warning_threshold = thresholds.hallucination_rate_warning
            critical_threshold = thresholds.hallucination_rate_critical
        elif metric_type in [MetricType.LATENCY_P50, MetricType.LATENCY_P99]:
            warning_threshold = mean + 2 * std_dev
            critical_threshold = mean + 3 * std_dev
        elif metric_type in [MetricType.ACCURACY, MetricType.GRADING_CONSISTENCY]:
            n = len(historical_values)
            p_hat = min(max(mean, 1e-10), 1 - 1e-10)
            z = 1.96
            denom = 1 + z**2 / n
            center = (p_hat + z**2 / (2 * n)) / denom
            margin = z * math.sqrt((p_hat * (1 - p_hat) + z**2 / (4 * n)) / n) / denom
            wilson_warning = max(0.0, center - margin)
            z_critical = 2.576
            denom_c = 1 + z_critical**2 / n
            center_c = (p_hat + z_critical**2 / (2 * n)) / denom_c
            margin_c = z_critical * math.sqrt((p_hat * (1 - p_hat) + z_critical**2 / (4 * n)) / n) / denom_c
            wilson_critical = max(0.0, center_c - margin_c)
            sigma_warning = mean - 2 * std_dev
            sigma_critical = mean - 3 * std_dev
            warning_threshold = max(wilson_warning, sigma_warning)
            critical_threshold = max(wilson_critical, sigma_critical)
        else:
            warning_threshold = mean - 2 * std_dev
            critical_threshold = mean - 3 * std_dev

        baseline = PerformanceBaseline(
            metric_type=metric_type,
            mean=mean,
            std_dev=std_dev,
            sample_size=len(historical_values),
            warning_threshold=warning_threshold,
            critical_threshold=critical_threshold,
            organ=organ,
            created_at=datetime.now().isoformat(),
        )

        self.save_baseline(baseline, organ)
        return baseline

    def detect_drift(
        self,
        metric_type: MetricType,
        current_value: float,
        organ: str = "all",
    ) -> DriftAlert | None:
        """
        检测单个指标是否发生漂移

        Args:
            metric_type: 指标类型
            current_value: 当前值
            organ: 器官类型

        Returns:
            Optional[DriftAlert]: 漂移警报，如果无漂移则返回None
        """
        baseline = self.load_baseline(metric_type, organ)
        if not baseline:
            logger.warning(
                "no_baseline_for_drift_detection",
                model_name=self.model_name,
                metric_type=metric_type.value,
                organ=organ,
            )
            return None

        deviation = abs(current_value - baseline.mean)
        deviation_pct = (deviation / baseline.mean * 100) if baseline.mean != 0 else 0

        severity = DriftSeverity.NONE
        message = ""

        if metric_type == MetricType.HALLUCINATION_RATE:
            if current_value > baseline.critical_threshold:
                severity = DriftSeverity.CRITICAL
                message = f"{metric_type.value} 严重漂移: {current_value:.4f} > {baseline.critical_threshold:.4f}"
            elif current_value > baseline.warning_threshold:
                severity = DriftSeverity.WARNING
                message = f"{metric_type.value} 警告漂移: {current_value:.4f} > {baseline.warning_threshold:.4f}"
        elif metric_type in [MetricType.LATENCY_P50, MetricType.LATENCY_P99]:
            # 越小越好：检查是否超过阈值
            if current_value > baseline.critical_threshold:
                severity = DriftSeverity.CRITICAL
                message = f"{metric_type.value} 严重漂移: {current_value:.4f} > {baseline.critical_threshold:.4f}"
            elif current_value > baseline.warning_threshold:
                severity = DriftSeverity.WARNING
                message = f"{metric_type.value} 警告漂移: {current_value:.4f} > {baseline.warning_threshold:.4f}"
        else:
            # 越大越好：检查是否低于阈值
            if current_value < baseline.critical_threshold:
                severity = DriftSeverity.CRITICAL
                message = f"{metric_type.value} 严重漂移: {current_value:.4f} < {baseline.critical_threshold:.4f}"
            elif current_value < baseline.warning_threshold:
                severity = DriftSeverity.WARNING
                message = f"{metric_type.value} 警告漂移: {current_value:.4f} < {baseline.warning_threshold:.4f}"

        if severity == DriftSeverity.NONE:
            return None

        alert = DriftAlert(
            severity=severity,
            metric_type=metric_type,
            current_value=current_value,
            baseline_mean=baseline.mean,
            deviation=deviation,
            deviation_pct=deviation_pct,
            organ=organ,
            timestamp=datetime.now().isoformat(),
            message=message,
        )

        logger.warning(
            "drift_detected",
            model_name=self.model_name,
            severity=severity.value,
            metric_type=metric_type.value,
            current_value=current_value,
            baseline_mean=baseline.mean,
            deviation_pct=deviation_pct,
            organ=organ,
        )

        return alert

    def run_detection(
        self,
        current_metrics: dict[MetricType, float],
        organ_metrics: dict[str, dict[MetricType, float]] | None = None,
        test_data_version: str = "",
    ) -> DriftReport:
        """
        运行完整的漂移检测

        Args:
            current_metrics: 当前指标值
            organ_metrics: 各器官的指标值
            test_data_version: 测试数据版本

        Returns:
            DriftReport: 漂移检测报告
        """
        report = DriftReport(
            model_name=self.model_name,
            model_version=self.model_version,
            test_timestamp=datetime.now().isoformat(),
            test_data_version=test_data_version,
        )

        logger.info(
            "drift_detection_started",
            model_name=self.model_name,
            model_version=self.model_version,
            test_data_version=test_data_version,
        )

        # 检测整体指标
        for metric_type, current_value in current_metrics.items():
            snapshot = MetricSnapshot(
                timestamp=datetime.now().isoformat(),
                metric_type=metric_type,
                value=current_value,
                organ="all",
                model_version=self.model_version,
            )
            report.snapshots.append(snapshot)

            baseline = self.load_baseline(metric_type, "all")
            if baseline:
                report.baselines[metric_type] = baseline

            alert = self.detect_drift(metric_type, current_value, "all")
            if alert:
                report.alerts.append(alert)

        # 检测各器官指标
        if organ_metrics:
            for organ, metrics in organ_metrics.items():
                for metric_type, current_value in metrics.items():
                    snapshot = MetricSnapshot(
                        timestamp=datetime.now().isoformat(),
                        metric_type=metric_type,
                        value=current_value,
                        organ=organ,
                        model_version=self.model_version,
                    )
                    report.snapshots.append(snapshot)

                    baseline = self.load_baseline(metric_type, organ)
                    if baseline and metric_type not in report.baselines:
                        report.baselines[metric_type] = baseline

                    alert = self.detect_drift(metric_type, current_value, organ)
                    if alert:
                        report.alerts.append(alert)

        # 计算整体状态
        report.overall_status = DriftSeverity.NONE
        for alert in report.alerts:
            if alert.severity == DriftSeverity.CRITICAL:
                report.overall_status = DriftSeverity.CRITICAL
                break
            elif alert.severity == DriftSeverity.WARNING:
                report.overall_status = DriftSeverity.WARNING

        logger.info(
            "drift_detection_completed",
            model_name=self.model_name,
            overall_status=report.overall_status.value,
            alert_count=len(report.alerts),
            critical_count=sum(1 for a in report.alerts if a.severity == DriftSeverity.CRITICAL),
            warning_count=sum(1 for a in report.alerts if a.severity == DriftSeverity.WARNING),
        )

        return report

    def report_to_dict(self, report: DriftReport) -> dict[str, Any]:
        """将漂移报告转换为字典"""
        return {
            "model_name": report.model_name,
            "model_version": report.model_version,
            "test_timestamp": report.test_timestamp,
            "test_data_version": report.test_data_version,
            "overall_status": report.overall_status.value,
            "alerts": [
                {
                    "severity": alert.severity.value,
                    "metric_type": alert.metric_type.value,
                    "current_value": alert.current_value,
                    "baseline_mean": alert.baseline_mean,
                    "deviation": alert.deviation,
                    "deviation_pct": alert.deviation_pct,
                    "organ": alert.organ,
                    "timestamp": alert.timestamp,
                    "message": alert.message,
                }
                for alert in report.alerts
            ],
            "snapshots": [
                {
                    "timestamp": snap.timestamp,
                    "metric_type": snap.metric_type.value,
                    "value": snap.value,
                    "organ": snap.organ,
                    "model_version": snap.model_version,
                }
                for snap in report.snapshots
            ],
            "baselines": {
                mt.value: {
                    "mean": b.mean,
                    "std_dev": b.std_dev,
                    "sample_size": b.sample_size,
                    "warning_threshold": b.warning_threshold,
                    "critical_threshold": b.critical_threshold,
                    "organ": b.organ,
                    "created_at": b.created_at,
                    "updated_at": b.updated_at,
                }
                for mt, b in report.baselines.items()
            },
        }

    def report_to_json(self, report: DriftReport, indent: int = 2) -> str:
        """将漂移报告转换为JSON"""
        return json.dumps(self.report_to_dict(report), indent=indent, ensure_ascii=False)
