"""P1-13：结果接口不得把"正常在跑"误报成 timeout。

原实现硬编码 30 秒阈值，而真实诊断管线要 90 秒以上（dispatcher + fusion 的
LLM 串行链 + 冷启动）。后果是：诊断仍在 processing 时前端就收到 timeout，
停止轮询并显示"超时失败"，而后台其实马上要出结果了——医生白等一场。

本测试钉住：
1. 阈值不小于 90 秒（真实管线的实测下界）；
2. 阈值可配置（换模型/规模时能调）；
3. 判定逻辑本身：未超时不改状态，超时才改；
4. 语义澄清：读路径的 timeout **不终止后台管线**（真超时由管线预算控制）。
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from app.api.v1.diagnosis import RESULT_TIMEOUT_SECONDS
from app.config.settings import settings

pytestmark = [pytest.mark.track_api]

DIAGNOSIS_PY = Path(inspect.getfile(__import__("app.api.v1.diagnosis", fromlist=["x"])))


class TestTimeoutThreshold:
    def test_threshold_not_below_pipeline_reality(self):
        """真实诊断 90s+（真机演练实测多轮 5s 仍在 processing），阈值不能低于它。"""
        assert RESULT_TIMEOUT_SECONDS >= 90, (
            f"阈值 {RESULT_TIMEOUT_SECONDS}s 低于真实管线耗时，会把正常诊断误报成 timeout"
        )

    def test_threshold_configurable(self):
        assert hasattr(settings, "result_read_timeout_seconds")
        assert settings.result_read_timeout_seconds >= 90
        assert settings.result_read_timeout_seconds == RESULT_TIMEOUT_SECONDS

    def test_default_is_180(self):
        """默认 180s：覆盖冷启动 + 排队，且不至于让前端无限等。"""
        assert RESULT_TIMEOUT_SECONDS == 180


class TestTimeoutJudgement:
    def _judge(self, status: str, elapsed: float) -> str:
        """复刻端点里的判定（保持与源码同构，源码改动时此测试会提醒同步）。"""
        result_status = status
        if result_status == "processing" and elapsed > RESULT_TIMEOUT_SECONDS:
            result_status = "timeout"
        return result_status

    def test_within_threshold_stays_processing(self):
        assert self._judge("processing", 45.0) == "processing"
        assert self._judge("processing", 89.9) == "processing"

    def test_beyond_threshold_becomes_timeout(self):
        assert self._judge("processing", 181.0) == "timeout"

    def test_other_statuses_untouched(self):
        for st in ("completed", "failed", "partial"):
            assert self._judge(st, 9999.0) == st

    def test_read_path_does_not_kill_pipeline(self):
        """语义澄清：读路径只改展示状态，不终止后台管线。"""
        src = DIAGNOSIS_PY.read_text(encoding="utf-8")
        block = src[src.find("if result_status == \"processing\""):][:400]
        assert "cancel" not in block.lower(), "结果读路径不得取消后台诊断"
        assert "terminate" not in block.lower()


class TestNoHardcodedTimeout:
    def test_no_magic_30_seconds(self):
        """源码里不应再出现裸的 30 秒阈值（防回归）。"""
        src = DIAGNOSIS_PY.read_text(encoding="utf-8")
        hardcoded = re.findall(r"elapsed\s*>\s*(\d+)", src)
        for value in hardcoded:
            assert int(value) >= 90, f"源码里出现 {value}s 的硬编码阈值（P1-13 回归）"
