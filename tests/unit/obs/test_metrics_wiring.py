"""埋点与契约断点守卫（quality-debt）。

为什么单独加这道守卫
--------------------
前两轮反复出现同一类缺陷，且都不是"写错代码"，而是"**定义了但没人接线**"：

1. `record_multi_organ_latency`、`ultrasound_model_hot_reload_success_rate` 等指标在
   `definitions.py` 里定义齐全、看板也在读，但**没有任何写入方**——看板上永远是 0，
   看起来"系统很健康"，实际是没埋点；
2. `app/api/v1/diagnosis.py` 的 `version_compatibility` 曾是死字符串 "compatible"，
   真实不兼容时接口照样报"兼容"。

这类缺陷靠 code review 抓不住（两边看着都对），只能靠机器守卫：定义与使用必须**成对**。

守卫范围
--------
- 每个已定义的指标，必须在 `definitions.py` 之外至少出现一次（写入或读取）；
- 看板读取的指标必须有写入方（否则读的是永远为 0 的幽灵指标）；
- 关键契约字段不得是硬编码占位值。
"""
import ast
import logging
import re
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_DIR = REPO_ROOT / "app"
DEFINITIONS_PATH = APP_DIR / "observability" / "metrics_collector" / "definitions.py"

# 允许"仅定义、无人引用"的指标：必须给理由，且理由不能是"懒得接"。
# 目前为空——若将来确需只暴露给外部抓取（Prometheus 直接 scrape，代码里不引用），
# 在此登记并说明。
SCRAPE_ONLY_METRICS: dict = {}

# 模块级 asyncio 原语的存量清单（棘轮）：只减不增。
# 改为"惰性按循环取锁"后请从这里移除，测试会检查基线是否同步收窄。
KNOWN_LOOP_BOUND_DEBT = frozenset(
    {
        "app/services/audit_service.py",
        "app/services/diagnosis_worker.py",
        "app/observability/audit_chain/critical_audit.py",
        "app/observability/audit_chain/cross_chain.py",
    }
)


def _defined_metrics() -> dict:
    """解析 definitions.py，返回 {属性名: 导出的指标名}。"""
    source = DEFINITIONS_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        r"self\.(?P<attr>\w+)\s*=\s*(?:Counter|Gauge|Histogram)\(\s*(?P<name>\"[^\"]+\"|\'[^\']+\')"
    )
    return {
        match.group("attr"): match.group("name").strip("\"'")
        for match in pattern.finditer(source)
    }


def _python_sources() -> dict:
    """除 definitions.py 之外的全部应用源码。"""
    return {
        path: path.read_text(encoding="utf-8", errors="ignore")
        for path in APP_DIR.rglob("*.py")
        if path != DEFINITIONS_PATH
    }


@pytest.fixture(scope="module")
def defined_metrics() -> dict:
    return _defined_metrics()


@pytest.fixture(scope="module")
def sources() -> dict:
    return _python_sources()


class TestMetricsHaveWriters:
    def test_metrics_are_defined_at_all(self, defined_metrics):
        """解析器失效会让本文件所有断言变成空转，先自证能解析到东西。"""
        assert len(defined_metrics) > 20, "未解析到指标定义，解析规则可能已失效"

    def test_every_metric_is_referenced_outside_definitions(self, defined_metrics, sources):
        """定义了却没人引用 = 看板上永远为 0 的幽灵指标。"""
        orphans = []
        for attr, metric_name in defined_metrics.items():
            if attr in SCRAPE_ONLY_METRICS or metric_name in SCRAPE_ONLY_METRICS.values():
                continue
            referenced = any(
                f".{attr}" in text or metric_name in text for text in sources.values()
            )
            if not referenced:
                orphans.append(f"{attr} ({metric_name})")

        assert orphans == [], (
            "下列指标已定义但全仓无写入/读取方，看板读到的是恒 0 的幽灵值：\n  "
            + "\n  ".join(sorted(orphans))
        )


class TestDashboardReadsRealMetrics:
    def test_dashboard_metrics_have_sources(self, defined_metrics, sources):
        """看板引用的指标必须有写入方，否则展示的是"永远健康"。"""
        dashboard = APP_DIR / "api" / "v1" / "obs_dashboard.py"
        if not dashboard.exists():
            pytest.skip("未找到看板模块")

        dashboard_source = dashboard.read_text(encoding="utf-8")
        referenced = {
            attr
            for attr, name in defined_metrics.items()
            if f".{attr}" in dashboard_source or name in dashboard_source
        }

        missing_writers = []
        for attr in sorted(referenced):
            metric_name = defined_metrics[attr]
            has_writer = any(
                f".{attr}" in text or f".{attr}." in text or metric_name in text
                for path, text in sources.items()
                if "obs_dashboard.py" not in str(path)
            )
            if not has_writer:
                missing_writers.append(attr)

        assert missing_writers == [], (
            f"看板读取但无写入方的指标：{missing_writers}"
        )


class TestContractFieldsAreNotPlaceholders:
    """关键契约字段不得是硬编码占位值。"""

    def test_version_compatibility_is_computed(self):
        """曾被硬编码成 "compatible"：真实不兼容时接口仍报兼容。

        注意断言要对准**真实接线**：接口层不直接调 ``is_compatible``，而是经
        ``validator.check_version_compatibility`` 转交（兼容矩阵的唯一来源在
        ``app.knowledge.version``）。只搜 is_compatible 会把正确的实现误判成没接线。
        """
        diagnosis_source = (APP_DIR / "api" / "v1" / "diagnosis.py").read_text(
            encoding="utf-8"
        )
        validator_source = (APP_DIR / "config" / "validator.py").read_text(encoding="utf-8")

        assert 'version_compatibility="compatible"' not in diagnosis_source, (
            "version_compatibility 又变回硬编码字符串了"
        )
        assert "check_version_compatibility" in diagnosis_source, (
            "接口层未调用兼容性判定，该字段可能仍是占位值"
        )
        assert "is_compatible" in validator_source, (
            "判定必须委托给兼容矩阵唯一来源 app.knowledge.version.is_compatible"
        )

    def test_startup_validates_version_compatibility(self):
        """启动时必须校验，否则不兼容组合要等到线上才发现。"""
        validator_source = (APP_DIR / "config" / "validator.py").read_text(encoding="utf-8")
        main_source = (APP_DIR / "main.py").read_text(encoding="utf-8")

        assert "validate_version_compatibility" in validator_source
        assert "validate_config" in main_source

    def test_diagnosis_result_exposes_compatibility_field(self):
        """字段必须真的出现在响应契约里，而不是只算不用。"""
        schemas = (APP_DIR / "models" / "schemas.py").read_text(encoding="utf-8")

        assert "version_compatibility" in schemas


class TestConsentGuardIsWired:
    """前端守卫不得只定义不接线（此前 consentGuard.ts 全仓无 import）。"""

    def test_page_consumes_consent_guard(self):
        page = REPO_ROOT / "frontend" / "src" / "pages" / "DiagnosisPage.tsx"
        if not page.exists():
            pytest.skip("未找到诊断结果页")

        source = page.read_text(encoding="utf-8")

        assert "isPureReportMode(" in source
        assert "getConsentDisplayText(" in source


class TestNoAsyncPrimitiveWithoutLoopBinding:
    """asyncio 原语的模块级单例必须绑定当前事件循环（历史上修过 3 处同类死锁）。

    为什么这是真问题：Python 3.10+ 的 ``asyncio.Lock`` 在**首次 acquire 时**绑定循环，
    之后换到另一个循环使用会直接 RuntimeError（pytest-asyncio 每个用例一个循环，
    测试套里就踩得到）。模块顶层创建 = 导入即固定生命周期，跨循环复用是必然风险。

    现状是**棘轮**而非豁免：以下 4 处为存量，必须逐轮改为"惰性按循环取锁"。
    新增任何一处，或基线未按修复进度调小，本测试都会失败。
    """

    def _offenders(self) -> list:
        offenders = []
        for path in APP_DIR.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:  # pragma: no cover - 坏文件交给编译器
                logger.warning("TestNoAsyncPrimitiveWithoutLoopBinding._offenders: 未预期异常，按原逻辑降级", exc_info=True)
                continue
            for node in tree.body:  # 只看模块顶层
                if not isinstance(node, ast.Assign):
                    continue
                value = node.value
                if isinstance(value, ast.Call) and (
                    isinstance(value.func, ast.Attribute)
                    and value.func.attr in {"Lock", "Event", "Queue", "Semaphore", "Condition"}
                    and isinstance(value.func.value, ast.Name)
                    and value.func.value.id == "asyncio"
                ):
                    offenders.append(str(path.relative_to(REPO_ROOT)).replace("\\", "/"))
        return sorted(set(offenders))

    def test_no_new_module_level_asyncio_primitives(self):
        offenders = self._offenders()

        assert set(offenders) <= KNOWN_LOOP_BOUND_DEBT, (
            "新增了模块顶层 asyncio 原语（跨循环会 RuntimeError）：\n  "
            + "\n  ".join(sorted(set(offenders) - KNOWN_LOOP_BOUND_DEBT))
        )

    def test_debt_baseline_shrinks_with_fixes(self):
        """修好一处就要从基线移除，否则棘轮会逐渐失真。"""
        offenders = self._offenders()

        stale = sorted(KNOWN_LOOP_BOUND_DEBT - set(offenders))

        assert stale == [], (
            f"下列文件已不再有模块级 asyncio 原语，请从基线移除：{stale}"
        )
