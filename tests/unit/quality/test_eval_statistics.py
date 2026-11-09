"""统计推断守卫 —— 钉住"这点差异到底是真是假"的算法。

## 为什么这组用例值得存在

本项目此前把 `0.5739 → 0.6071` 这类差值直接当"改进"报过。在 n=115 的配对
比较里，5pt 差异**经常判不动**（见学习报告的功效分析表）。如果统计层本身算错，
"判不动"与"判得动"会被混淆，等于把噪声包装成结论。

这里钉四件事：
1. **McNemar 精确解与暴力解逐位一致**（含大 n，防溢出回退成错的近似）；
2. **功效分析的闭式解与精确解的方向**（闭式偏乐观 ~7%，对外报数必须用精确版）；
3. **bootstrap 的确定性**（固定种子 → 同一输入永远同一区间，报告可复算）；
4. **"判不动"要能被正确识别**（区间含 0 / p>alpha 时有明确的返回值，不靠读 log）。
"""
from __future__ import annotations

from math import comb

import pytest

from app.evaluation.statistics import (
    bonferroni_alpha,
    bootstrap_ci,
    exact_mcnemar_power,
    mcnemar_exact,
    mcnemar_from_paired,
    mcnemar_power,
    min_detectable_delta,
    paired_bootstrap_diff,
    required_n_for_delta,
    required_n_for_delta_exact,
    two_proportion_n,
)


# --------------------------------------------------------------- McNemar 精确性
class TestMcNemarExact:
    @staticmethod
    def _brute(b: int, c: int) -> float:
        n = b + c
        if n == 0:
            return 1.0
        k = min(b, c)
        return min(1.0, 2 * sum(comb(n, i) * 0.5 ** n for i in range(k + 1)))

    @pytest.mark.parametrize(
        "b,c",
        [(0, 0), (0, 5), (1, 1), (3, 7), (10, 2), (5, 5), (12, 10), (20, 21), (9, 1)],
    )
    def test_matches_brute_force(self, b, c):
        assert mcnemar_exact(b, c) == pytest.approx(self._brute(b, c), abs=1e-12)

    def test_symmetric_direction_gives_no_evidence(self):
        """b == c 时方向完全对称，不可能有差异证据。"""
        for k in (1, 5, 10, 40):
            assert mcnemar_exact(k, k) == pytest.approx(1.0, abs=1e-9)

    def test_larger_asymmetry_gives_smaller_p(self):
        ps = [mcnemar_exact(10 + d, 10) for d in (0, 2, 5, 10, 20)]
        assert ps == sorted(ps, reverse=True)

    def test_no_overflow_at_large_n(self):
        """2026-11-09 实测：直接 comb() 在 n>1000 时 OverflowError。

        这里同时钉住"大 n 仍能给出正确量级"：n=1400 时应到 1e-7 量级，
        若退化成错误的近似会明显偏离。
        """
        assert 0.0 <= mcnemar_exact(700, 600) <= 1.0
        assert mcnemar_exact(800, 600) < 1e-6

    def test_zero_cell_returns_one(self):
        assert mcnemar_exact(0, 0) == 1.0

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            mcnemar_exact(-1, 2)


class TestMcNemarFromPaired:
    def test_counts_discordant_cells(self):
        a = [True, True, True, False, False]
        b = [False, True, True, True, False]
        # b_only_a: a=True,b=False -> 1 条（索引0）
        # c_only_b: a=False,b=True -> 1 条（索引3）
        r = mcnemar_from_paired(a, b)
        assert r.b == 1 and r.c == 1
        assert r.n_total == 5
        assert r.delta == pytest.approx(0.0)
        assert r.p_value == pytest.approx(1.0)

    def test_delta_is_absolute_difference(self):
        a = [True] * 8 + [False] * 2
        b = [True] * 3 + [False] * 7
        r = mcnemar_from_paired(a, b)
        assert r.delta == pytest.approx(0.5)
        assert r.b == 5 and r.c == 0

    def test_length_mismatch_rejected(self):
        with pytest.raises(ValueError):
            mcnemar_from_paired([True], [True, False])


# --------------------------------------------------------------- 功效分析
class TestPowerAnalysis:
    def test_closed_form_is_competitive_not_authoritative(self):
        """闭式解比精确解小（**偏乐观**）—— 这是它不能对外报数的原因。"""
        for d in (0.10, 0.15, 0.20):
            n_closed = required_n_for_delta(0.05, d)
            n_exact = required_n_for_delta_exact(0.05, d)
            assert n_closed <= n_exact, (d, n_closed, n_exact)

    def test_exact_power_reaches_target_at_exact_n(self):
        """精确解给的 n 处，功效必须真的 ≥ 0.80（不是"接近"）。"""
        for d in (0.10, 0.20):
            n = required_n_for_delta_exact(0.05, d)
            assert exact_mcnemar_power(n, 0.05, d) >= 0.80
            # 前一个 n 应该达不到，证明这是"最小"的不是"随便一个大数"
            assert exact_mcnemar_power(n - 30, 0.05, d) < 0.80

    def test_power_increases_with_n(self):
        powers = [exact_mcnemar_power(n, 0.05, 0.15) for n in (200, 300, 400, 500)]
        assert powers == sorted(powers)

    def test_headline_numbers_for_report(self):
        """报告里引用的核心数字钉在这里，改实现必须同步改报告。"""
        assert required_n_for_delta_exact(0.05, 0.15) == 487
        assert required_n_for_delta_exact(0.05, 0.10) == 333
        assert required_n_for_delta_exact(0.05, 0.25) == 787

    def test_discordant_rate_must_exceed_delta(self):
        with pytest.raises(ValueError):
            required_n_for_delta(0.10, 0.05)

    def test_min_detectable_delta_shrinks_with_n(self):
        d_small = min_detectable_delta(115, 0.15)
        d_big = min_detectable_delta(700, 0.15)
        assert d_small is not None and d_big is not None
        assert d_big < d_small

    def test_min_detectable_delta_none_when_too_small(self):
        assert min_detectable_delta(12, 0.15) is None

    def test_two_proportion_is_much_larger_than_paired(self):
        """独立设计 vs 配对设计差一个数量级，用错会把需求估高。"""
        n_paired = required_n_for_delta(0.05, 0.15)
        n_indep = two_proportion_n(0.60, 0.55)
        assert n_indep > n_paired * 3

    def test_closed_form_power_is_optimistic(self):
        """明确记录闭式解的偏差方向与量级（约 2~4pt 的虚高）。"""
        n = required_n_for_delta(0.05, 0.15)
        approx = mcnemar_power(n, 0.05, 0.15)
        exact = exact_mcnemar_power(n, 0.05, 0.15)
        assert approx > exact          # 近似值偏乐观
        assert 0.75 < exact < 0.81


# --------------------------------------------------------------- bootstrap
class TestBootstrap:
    def test_deterministic_with_fixed_seed(self):
        v = [0.1, 0.5, 0.9, 1.0, 0.3, 0.7]
        assert bootstrap_ci(v) == bootstrap_ci(v)

    def test_interval_brackets_point_estimate(self):
        v = [0.2, 0.4, 0.6, 0.8, 1.0]
        lo, hi = bootstrap_ci(v)
        assert lo <= 0.6 <= hi

    def test_wider_for_noisier_data(self):
        narrow = bootstrap_ci([0.50, 0.51, 0.49, 0.50, 0.50])
        wide = bootstrap_ci([0.0, 0.0, 1.0, 1.0, 0.5])
        assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])

    def test_empty_input_returns_zeros(self):
        assert bootstrap_ci([]) == (0.0, 0.0)

    def test_paired_diff_detects_zero_when_same(self):
        a = [1.0, 0.0, 1.0, 0.0]
        r = paired_bootstrap_diff(a, a)
        assert r["diff"] == pytest.approx(0.0)
        assert r["excludes_zero"] is False

    def test_paired_diff_excludes_zero_when_consistently_better(self):
        a = [1.0] * 40
        b = [0.0] * 40
        r = paired_bootstrap_diff(a, b)
        assert r["diff"] == pytest.approx(1.0)
        assert r["excludes_zero"] is True

    def test_paired_diff_length_mismatch(self):
        with pytest.raises(ValueError):
            paired_bootstrap_diff([1.0], [1.0, 2.0])


# --------------------------------------------------------------- 多重比较
def test_bonferroni():
    assert bonferroni_alpha(5) == pytest.approx(0.01)
    assert bonferroni_alpha(1) == pytest.approx(0.05)
    with pytest.raises(ValueError):
        bonferroni_alpha(0)


def test_statistics_module_has_no_third_party_imports():
    """纯标准库是本模块能在任何环境（含离线评测机）跑起来的前提。"""
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[3] / "app/evaluation/statistics.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    allowed = {"__future__", "math", "random", "dataclasses", "collections", "typing",
               "functools"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] in allowed, node.module


def test_normal_quantile_accuracy():
    """`_z_for` 的有理逼近误差 <4.5e-4，钉住常用分位避免手误。"""
    from app.evaluation.statistics import _z_for

    # Abramowitz-Stegun 26.2.23 的有理逼近误差 <4.5e-4，实测 0.975 处偏 4.3e-4，
    # 故容差取 5e-4（这是该逼近的固有精度，不是实现缺陷；需要更高精度就换查表）。
    assert _z_for(0.975) == pytest.approx(1.959964, abs=5e-4)
    assert _z_for(0.80) == pytest.approx(0.841621, abs=5e-4)
    assert _z_for(0.995) == pytest.approx(2.575829, abs=5e-3)
    assert _z_for(0.025) == pytest.approx(-1.959964, abs=5e-4)
