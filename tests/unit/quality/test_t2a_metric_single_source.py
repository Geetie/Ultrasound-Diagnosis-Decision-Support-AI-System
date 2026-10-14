"""A.64 WP1 守卫：T2a 数字必须**同一把尺子**，且口径判定只能落到四选一。

为什么值得钉住（都是真机上踩出来的）：
1. **四条链四套 AUC 实现**（`roc_auc_score` ×2 / 自写 `_auc` / p29 的秩和）→ 数字不可比，
   这是 A.63「0.4929 vs 0.6789」调查里最先暴露的结构性问题。新探针**只准**
   从 `p29_t2a_aul_holdout` import，禁止本地再写一份 AUC。
2. **口径判定必须落到四选一**（复现 / 标签反向 / 孤儿作废 / 数据缺失）——否则又是
   "看起来查过"。这里直接对判定函数下料，四种输入各验一次。
3. **AUC≈0.5 时换阈值救不回判别力**：Youden 腿要能区分"阈值不对"与"排序无判别力"。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_AUDIT = _REPO / "scripts" / "audit"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_{name}_under_test", _AUDIT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_p35_and_p37_reuse_the_same_auc_implementation():
    """两条探针都必须 import p29 的指标实现，不许本地重写（同尺子）。"""
    for name in ("p35_thyroid_cross_source_eval", "p37_t2a_tn3k_parity"):
        src = (_AUDIT / f"{name}.py").read_text(encoding="utf-8")
        assert "from p29_t2a_aul_holdout import" in src, f"{name} 必须复用 p29 的指标口径"
        assert "auc_mal_vs_rest" in src
        # 不允许自己实现 AUC / bootstrap（那就又变出一把新尺子）
        assert "def auc" not in src, f"{name} 不得自写 AUC"
        assert "def bootstrap" not in src, f"{name} 不得自写 bootstrap"


def test_verdict_falls_into_exactly_four_states():
    p37 = _load("p37_t2a_tn3k_parity")

    code, _ = p37._verdict(None, 614, 0.4929, 0.5071)
    assert code == "ORPHAN_UNREPRODUCIBLE"  # 无对照物 → 也走孤儿分支

    code, why = p37._verdict({"malignancy_auc": 0.4929, "n": 614}, 614, 0.4929, 0.5071)
    assert code == "SAME_METRIC_REPRODUCED" and "复现" in why

    code, why = p37._verdict({"malignancy_auc": 0.5071, "n": 614}, 614, 0.4929, 0.5071)
    assert code == "LABEL_DIRECTION_MISMATCH" and "反向" in why

    code, why = p37._verdict({"malignancy_auc": 0.6789, "n": 1228}, 614, 0.4929, 0.5071)
    assert code == "ORPHAN_UNREPRODUCIBLE" and "作废" in why


def test_youden_separates_threshold_issue_from_no_ranking_power():
    p37 = _load("p37_t2a_tn3k_parity")
    # 可分：恶性分数一律更高 → 存在阈值同时拿到 sens=spec=1.0
    scores = [0.9, 0.8, 0.2, 0.1]
    labels = ["Malignant", "Malignant", "Benign", "Benign"]
    _thr, sens, spec = p37._youden_threshold(scores, labels)
    assert sens == 1.0 and spec == 1.0
    # 不可分（分数全部相同）→ 任何阈值都救不回（sens+spec=1.0 且阈值为 0.5 之外的情形不成立）
    flat = [0.5, 0.5, 0.5, 0.5]
    _, sens2, spec2 = p37._youden_threshold(flat, labels)
    assert sens2 + spec2 == 1.0, "分数不可分时 Youden 只能到 1.0（证明不是阈值问题）"
