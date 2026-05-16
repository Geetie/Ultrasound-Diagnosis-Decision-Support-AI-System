from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import structlog

from app.observability.constants import CUSUMConstants

_logger = structlog.get_logger("app.observability.cusum")


@dataclass
class CUSUMCalibrationResult:
    h: float
    arl_0: float
    arl_1: float
    delta: float
    target_rate: float
    is_valid: bool
    recommendation: str


class CUSUMCalibrator:
    """CUSUM参数校准器

    使用蒙特卡洛仿真方法校准CUSUM参数(h, delta)，
    计算平均运行长度(ARL0/ARL1)，判定规则：ARL0>=100且ARL1<=50。
    支持numpy加速和纯Python回退。
    """

    def __init__(
        self,
        target_rate: float = 0.005,
        daily_cases: int = 50,
        simulation_days: int = 30,
        num_simulations: int = 1000,
    ):
        self._target_rate = target_rate
        self._daily_cases = daily_cases
        self._simulation_days = simulation_days
        self._num_simulations = num_simulations

    def calibrate(
        self,
        h_values: Optional[List[float]] = None,
        deltas: Optional[List[float]] = None,
    ) -> List[CUSUMCalibrationResult]:
        if h_values is None:
            h_values = [2.0, 3.0, 4.0, 5.0]
        if deltas is None:
            deltas = CUSUMConstants.CALIBRATION_DEFAULT_DELTAS

        results = []
        for h in h_values:
            arl_0 = self._simulate_arl(self._target_rate, h, 0.0)

            for delta in deltas:
                shifted_rate = self._target_rate + delta
                arl_1 = self._simulate_arl(shifted_rate, h, delta)

                is_valid = arl_0 >= 100 and arl_1 <= 50

                if arl_0 < 100:
                    recommendation = f"h={h} too low (ARL0={arl_0:.1f}<100), increase to h={h+1} or h={h+2}"
                elif arl_1 > 50:
                    recommendation = f"h={h} too high (ARL1={arl_1:.1f}>50), decrease to h={max(h-1, 1)}"
                else:
                    recommendation = f"h={h} valid (ARL0={arl_0:.1f}>=100, ARL1={arl_1:.1f}<=50)"

                results.append(CUSUMCalibrationResult(
                    h=h,
                    arl_0=arl_0,
                    arl_1=arl_1,
                    delta=delta,
                    target_rate=self._target_rate,
                    is_valid=is_valid,
                    recommendation=recommendation,
                ))

        return results

    def _simulate_arl(self, rate: float, h: float, delta: float) -> float:
        try:
            import numpy as np
            return self._simulate_arl_numpy(rate, h, delta)
        except ImportError:
            return self._simulate_arl_pure(rate, h, delta)

    def _simulate_arl_numpy(self, rate: float, h: float, delta: float) -> float:
        import numpy as np

        rng = np.random.default_rng(42)
        k = self._target_rate + delta / 2
        max_observations = self._simulation_days * self._daily_cases

        all_observations = rng.random((self._num_simulations, max_observations)) < rate
        increments = np.where(all_observations, 1.0 - k, -k)

        run_lengths = np.full(self._num_simulations, max_observations * 2, dtype=np.float64)

        s_plus = np.zeros(self._num_simulations)
        triggered = np.zeros(self._num_simulations, dtype=bool)

        for j in range(max_observations):
            s_plus = np.maximum(0.0, s_plus + increments[:, j])
            newly_triggered = (~triggered) & (s_plus > h)
            if np.any(newly_triggered):
                run_lengths[newly_triggered] = j + 1
                triggered |= newly_triggered
            s_plus[triggered] = 0.0
            if np.all(triggered):
                break

        triggered_runs = run_lengths[triggered]
        if len(triggered_runs) == 0:
            return float(max_observations * 2)

        return float(np.mean(triggered_runs))

    def _simulate_arl_pure(self, rate: float, h: float, delta: float) -> float:
        import random

        rng = random.Random(42)
        k = self._target_rate + delta / 2
        max_observations = self._simulation_days * self._daily_cases
        run_lengths = []

        for _ in range(self._num_simulations):
            s_plus = 0.0
            triggered = False
            run_length = 0

            for _ in range(max_observations):
                x = 1.0 if rng.random() < rate else 0.0
                s_plus = max(0.0, s_plus + (x - k))
                run_length += 1

                if s_plus > h:
                    triggered = True
                    break

            if triggered:
                run_lengths.append(run_length)
            else:
                run_lengths.append(max_observations * 2)

        triggered_runs = [rl for rl in run_lengths if rl <= max_observations]
        if not triggered_runs:
            return float(max_observations * 2)

        return sum(triggered_runs) / len(triggered_runs)

    def generate_calibration_report(self, results: List[CUSUMCalibrationResult]) -> Dict[str, Any]:
        valid_results = [r for r in results if r.is_valid]
        best = min(valid_results, key=lambda r: r.arl_1) if valid_results else None

        return {
            "target_rate": self._target_rate,
            "daily_cases": self._daily_cases,
            "simulation_days": self._simulation_days,
            "num_simulations": self._num_simulations,
            "total_configurations_tested": len(results),
            "valid_configurations": len(valid_results),
            "recommended_h": best.h if best else None,
            "recommended_delta": best.delta if best else None,
            "recommended_arl_0": best.arl_0 if best else None,
            "recommended_arl_1": best.arl_1 if best else None,
            "all_results": [
                {
                    "h": r.h,
                    "delta": r.delta,
                    "arl_0": round(r.arl_0, 1),
                    "arl_1": round(r.arl_1, 1),
                    "is_valid": r.is_valid,
                    "recommendation": r.recommendation,
                }
                for r in results
            ],
        }

    def save_calibration_report(self, report: Dict[str, Any], path: str = "data/cusum_calibration_report.json") -> None:
        import json
        import os
        from datetime import datetime, timezone

        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        _logger.info("cusum_calibration_report_saved", path=path)
        self._audit_calibration_report(report)

    @staticmethod
    def _audit_calibration_report(report: Dict[str, Any]) -> None:
        try:
            from app.observability.audit_chain import CriticalOperationAudit
            audit = CriticalOperationAudit(
                operation="cusum_calibration",
                operator="system",
                detail=report,
            )
            audit.log()
        except Exception:
            pass

    @staticmethod
    def load_calibration_report(path: str = "data/cusum_calibration_report.json") -> Optional[Dict[str, Any]]:
        import json
        import os

        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
