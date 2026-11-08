"""A.166/A.169：数据集台账必须存在且结构完整（防"指标无源可查"）。

## 为什么要这条守卫

2026-11-08 之前，项目里没有任何地方记录"这个指标用的是哪份数据、这份数据怎么造的、
能测什么不能测什么"。结果 `kb_qa_golden_v1`（含影像术语仅 11.8%）被用了半年，
报出 hit@10=98.53%，一路当成成绩对外汇报，直到有人逐条读原文才发现。

台账 + 这条守卫的作用：**指标引用哪个数据集，必须能在台账里查到它的来历与边界。**
"""

from __future__ import annotations

import json
import pathlib

import pytest

pytestmark = pytest.mark.track_quality

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_LEDGER = _ROOT / "docs" / "合规" / "评测数据集" / "数据集台账.md"
_README = _ROOT / "docs" / "合规" / "评测数据集" / "README.md"
_FEATURES = _ROOT / "feature_list.json"


def test_ledger_file_exists():
    assert _LEDGER.exists(), "数据集台账缺失——当前主线是数据集，没有台账等于不知道能测什么"


def test_ledger_has_all_three_categories():
    text = _LEDGER.read_text(encoding="utf-8")
    # 三分法：公开集 / 自己造 / 已作废
    assert "公开" in text
    assert "必须我们自己造" in text or "必须自己造" in text
    assert "已作废" in text or "已经作废" in text


def test_every_dataset_states_why_it_is_needed():
    """每份数据必须写清"要用它干什么"。"""
    text = _LEDGER.read_text(encoding="utf-8")
    # 台账里每个 A1~A8 / B1~B4 条目都应有"要用它干什么"
    for tag in ("A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"):
        assert tag in text, f"台账缺 {tag}"
    assert text.count("要用它干什么") >= 8, "每份数据都得说明用途"


def test_deprecated_datasets_are_listed():
    """8 个作废数据集必须全部列出，防止有人重新捡起来用。"""
    text = _LEDGER.read_text(encoding="utf-8")
    for name in (
        "kb_qa_golden_v1",
        "kb_golden_v1",
        "us365k_retrieval_golden_v1",
        "clean_eval_set_20261020",
        "double_engine_eval_set",
        "restored_non_echo_20261021",
        "consistency_test_cases",
    ):
        assert name in text, f"作废数据集 {name} 未在台账登记"


def test_readme_exists_with_manifest_spec():
    assert _README.exists()
    text = _README.read_text(encoding="utf-8")
    assert "manifest" in text.lower()
    assert "quality_checks" in text or "质量自查" in text


def test_feature_list_points_to_ledger():
    """feature_list 必须指向台账，保证单一事实源。"""
    data = json.loads(_FEATURES.read_text(encoding="utf-8"))
    target = None
    for f in data["features"]:
        if f.get("id") == "eval-dataset-rebuild":
            target = f
            break
    assert target is not None, "eval-dataset-rebuild feature 缺失"
    assert target.get("status") == "in_progress", "当前主线必须是 in_progress"
    assert target.get("dataset_ledger") == "docs/合规/评测数据集/数据集台账.md"
    ds = target.get("datasets") or {}
    assert len(ds.get("public_ready", [])) >= 3
    assert len(ds.get("must_build", [])) >= 4


def test_agents_md_routes_to_ledger():
    """AGENTS.md 的 Startup Workflow 必须提台账（否则新 Agent 找不到）。"""
    text = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "数据集台账" in text
