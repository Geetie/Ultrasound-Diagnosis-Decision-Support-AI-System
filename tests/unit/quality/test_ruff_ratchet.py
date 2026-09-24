"""ruff 质量债棘轮（quality-debt）。

设计
----
- **永久豁免**只在 ``ruff.toml``（行宽、中文标点）；
- **存量债务**登记在仓库根 ``.ruff-debt.txt``（一行一个规则码），CI 用它做 CLI ignore
  实现"新增违规立刻阻断"；
- 本测试用**不带任何 ignore 的完整检查**统计存量，并按规则码钉住计数上限。

为什么不用 ruff.toml 的 ignore 承载债务
---------------------------------------
实测踩到过：把债务码写进 ruff.toml 的 ignore 后，``ruff check --statistics`` 里这些
规则**彻底消失**（计数变成 0），棘轮失去测量对象，等于把债务藏起来；同时被 ignore 的
规则会让仓库里既有的 ``# noqa`` 变成"无用指令"，凭空多出 RUF100。

三条不变量
----------
1. 出现 .ruff-debt.txt 之外的违规规则 -> 失败（不能悄悄新增债务类别）；
2. 任一债务规则计数高于基线 -> 失败（不能新增同类违规）；
3. 计数低于基线 -> 提示把基线调小（锁住成果，避免棘轮失真）。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEBT_FILE = REPO_ROOT / ".ruff-debt.txt"

# 存量基线（2026-09-24 收敛后实测）。**只允许调小。**
RUFF_BASELINE = {
    # 225 -> 226：tests/unit/data 与 tests/integration/data 之前被 gitignore 覆盖，
    # 而 **ruff 默认遵循 .gitignore**，所以这几个文件对 ruff 也一直是"隐形"的。
    # 把它们纳入版本控制后统计里多出 53 条（已自动修掉 52 条，仅剩 1 条 E402）。
    # 这是"存量从看不见变成看得见"，不是新写的债。
    "E402": 226,    # conftest 的 env-before-import 与 main.py 的延迟导入都是刻意为之
    "F841": 62,     # 未使用局部变量
    "RUF013": 48,   # 隐式 Optional
    "RUF012": 47,   # 类级可变默认值
    "B904": 42,     # except 中 raise 未用 from
    "F401": 42,     # 多为 __init__.py 的再导出
    "SIM105": 39,   # 可用 contextlib.suppress
    "SIM117": 34,   # 可合并的 with
    "RUF059": 32,   # 解包后未使用的变量
    "SIM102": 22,   # 可折叠的 if
    "B007": 20,     # 循环变量未使用
    "SIM108": 13,   # 可用三元表达式
    "E701": 12,     # 单行多语句
    "B017": 11,     # assertRaises(Exception)
    "RUF046": 9,    # 多余的 int() 转换
    "B905": 8,      # zip 未指定 strict
    "RUF043": 6,    # pytest.raises 模式未转义
    "RUF005": 5,    # 集合字面量拼接
    "RUF006": 5,    # asyncio 悬挂任务
    "SIM103": 5,    # 可直接返回布尔表达式
    "UP035": 4,     # 已弃用导入
    "SIM110": 3,    # 可用 any()
    "E741": 2,      # 易混淆变量名
    "RUF022": 2,    # __all__ 未排序
    "E722": 1,      # 裸 except
    "F811": 1,      # 重复定义
    "I001": 1,      # 导入未排序
    "SIM101": 1,    # 重复 isinstance
    "SIM113": 1,    # 可用 enumerate
    "SIM118": 1,    # in dict.keys()
    "SIM222": 1,    # or True 表达式
    "W291": 1,      # 行尾空白
}


def _ruff_available() -> bool:
    if shutil.which("ruff") is not None:
        return True
    probe = subprocess.run(
        ["python", "-m", "ruff", "--version"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    return probe.returncode == 0


def _collect_statistics() -> dict:
    """完整检查的按规则计数（不带任何 CLI ignore，保证债务仍然可见）。"""
    completed = subprocess.run(
        ["python", "-m", "ruff", "check", ".", "--statistics"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    statistics = {}
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        count_token, code_token = parts[0], parts[1]
        # 规则码形如 E402 / RUF046 / SIM105：以字母开头且含数字，
        # 用 isalpha() 判断会把它们全部漏掉（还会静默 skip，比报错更糟）。
        if not count_token.isdigit():
            continue
        if not (code_token[0].isalpha() and code_token.isalnum()):
            continue
        statistics[code_token] = int(count_token)
    return statistics


def _debt_codes() -> list:
    lines = DEBT_FILE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


@pytest.fixture(scope="module")
def statistics() -> dict:
    if not _ruff_available():
        pytest.skip("ruff 未安装，跳过棘轮检查")
    data = _collect_statistics()
    if not data:
        pytest.skip("ruff 未产出统计（可能已全部收敛）")
    return data


class TestDebtRegistry:
    def test_debt_file_matches_baseline(self):
        """两份清单必须同口径：否则 CI 阻断范围与棘轮基线会悄悄错位。"""
        assert set(_debt_codes()) == set(RUFF_BASELINE)

    def test_debt_file_is_machine_readable(self):
        """CI 会直接把文件内容拼成 --ignore 参数，不能有注释或空行混入内容。"""
        for line in DEBT_FILE.read_text(encoding="utf-8").splitlines():
            assert not line.startswith("#"), "CI 会把 # 当作规则码传给 ruff"

    def test_debt_file_has_no_duplicates(self):
        codes = _debt_codes()

        assert len(codes) == len(set(codes))


class TestRuffRatchet:
    def test_no_rule_outside_debt_registry(self, statistics):
        """出现清单之外的新违规：要么立刻修掉，要么显式登记（登记即需说明理由）。"""
        unexpected = sorted(set(statistics) - set(_debt_codes()))

        assert unexpected == [], f"出现债务清单之外的新违规类别：{unexpected}"

    def test_no_rule_grew_beyond_baseline(self, statistics):
        """任一规则计数高于基线即失败——"只减不增"的落点。"""
        grew = {
            code: (statistics.get(code, 0), RUFF_BASELINE[code])
            for code in RUFF_BASELINE
            if statistics.get(code, 0) > RUFF_BASELINE[code]
        }

        assert grew == {}, f"违规数超过基线（规则: 当前/基线）：{grew}"

    def test_improvements_are_recorded(self, statistics):
        """变少是好事，但基线要同步调小，否则棘轮会逐渐失真。"""
        improved = {
            code: (statistics.get(code, 0), RUFF_BASELINE[code])
            for code in RUFF_BASELINE
            if statistics.get(code, 0) < RUFF_BASELINE[code]
        }

        assert improved == {}, f"下列规则已少于基线，请把基线调小以锁住成果：{improved}"


class TestConfig:
    def test_ruff_config_has_no_debt_ignores(self):
        """债务不得写回 ruff.toml —— 写回去会让计数消失，棘轮失去测量对象。"""
        config = (REPO_ROOT / "ruff.toml").read_text(encoding="utf-8")
        ignore_block = config.split("ignore = [", 1)[1].split("]", 1)[0]

        for code in _debt_codes():
            assert f'"{code}"' not in ignore_block, (
                f"{code} 出现在 ruff.toml 的 ignore 里：存量会变成不可见"
            )

    def test_bugbear_immutable_calls_keeps_fastapi_dependencies(self):
        """删掉这条配置会让百余条 B008 噪声重新淹没有效告警。"""
        config = (REPO_ROOT / "ruff.toml").read_text(encoding="utf-8")

        assert "extend-immutable-calls" in config
        assert "fastapi.Depends" in config

    def test_ci_uses_debt_file_for_blocking(self):
        """CI 必须真的用这份清单——只写文件不接线等于没有门禁。"""
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        assert ".ruff-debt.txt" in workflow, "CI 未引用债务清单，阻断范围与棘轮会错位"
