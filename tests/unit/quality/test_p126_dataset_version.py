"""p126 数据集版本化与覆盖矩阵的判据守护。

## 这份测试存在的理由

**交付项 5（版本化）**：报出「hit@10 = 0.7872」时必须能追溯是哪个数据集版本算的。
没有版本，数据集一改旧数字就失去意义。

**交付项 3（覆盖矩阵）**：要求每格 ≥10 条。没机器检查就等于没有。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.track_quality

_MOD = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p126_dataset_version.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("p126", _MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestDims:
    def test_four_dims(self, mod):
        """四维度必须齐 —— 少一个就测不出那一维的差异。"""
        assert mod.DIMS == ("hop", "specificity", "persona", "length")


class TestFingerprint:
    def test_same_content_same_fp(self, mod):
        a = [{"query": "问题一"}, {"query": "问题二"}]
        b = [{"query": "问题二"}, {"query": "问题一"}]   # 顺序不同
        assert mod.compute_fingerprint(a) == mod.compute_fingerprint(b)

    def test_diff_content_diff_fp(self, mod):
        a = [{"query": "问题一"}]
        b = [{"query": "问题二"}]
        assert mod.compute_fingerprint(a) != mod.compute_fingerprint(b)

    def test_fp_ignores_answers(self, mod):
        """指纹标识「这批问题」，不因标注修订而变。"""
        a = [{"query": "问题一", "reference_answer": "旧答案"}]
        b = [{"query": "问题一", "reference_answer": "新答案"}]
        assert mod.compute_fingerprint(a) == mod.compute_fingerprint(b)


class TestCoverage:
    def test_counts_cells(self, mod):
        rows = [
            {"hop": "single", "specificity": "specific", "persona": "resident", "length": "short"},
            {"hop": "single", "specificity": "specific", "persona": "resident", "length": "short"},
            {"hop": "multi", "specificity": "abstract", "persona": "qa", "length": "long"},
        ]
        cov = mod.coverage(rows)
        assert cov["cells"][("single", "specific", "resident", "short")] == 2
        assert cov["cells"][("multi", "abstract", "qa", "long")] == 1
        assert cov["missing_dims"] == {}

    def test_nested_metadata_supported(self, mod):
        """兼容 metadata 嵌套布局。"""
        rows = [{"metadata": {"hop": "single", "specificity": "specific",
                              "persona": "resident", "length": "short"}}]
        cov = mod.coverage(rows)
        assert cov["cells"][("single", "specific", "resident", "short")] == 1

    def test_missing_dim_reported(self, mod):
        rows = [{"hop": "single"}]
        cov = mod.coverage(rows)
        assert cov["missing_dims"]["specificity"] == 1
        assert cov["missing_dims"]["persona"] == 1
