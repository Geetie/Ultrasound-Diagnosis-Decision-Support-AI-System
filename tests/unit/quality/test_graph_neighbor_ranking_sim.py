"""A.65 WP1 守卫：图谱邻居排序仿真的**预注册判据**与**单一度量尺子**。

钉住两件事：
1. **预注册判据只能落到 GO/NO_GO 两种**（P1 不回退 + P2 排序确实比"任取"好），
   且两条必须**同时**满足才 GO —— 不许事后放宽（A.63/A.64 的教训：直觉合理的改动会被数据否掉，
   所以判据必须写死）；
2. **度量只许一把尺子**：doc 级命中复用 `gate2_bge_top10.doc_level_hit_at_k`、
   嵌入与余弦复用 `t1_eval_retrieval.{embed_texts, cosine_topk}`，禁止本探针自造。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_AUDIT = Path(__file__).resolve().parents[3] / "scripts" / "audit"


def _load():
    spec = importlib.util.spec_from_file_location(
        "_p39_under_test", _AUDIT / "p39_graph_neighbor_ranking_sim.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verdict_requires_all_three_preregistered_criteria():
    p39 = _load()
    # 三条全过（扩边有召回增益 + 不退化 + 排序有效）→ GO
    code, why = p39.verdict_of(0.90, 0.70, 0.90, 0.80, 0.90)
    assert code == "GO" and all(tag in why for tag in ("P0", "P1", "P2"))
    # **真机首跑的真实数字**：池子召回 0.9853 → 0.9853（零增益）但排序不退化、且远好于任取
    # → 必须判 NO_GO_NO_RECALL_GAIN（这条是回归锚点：首跑时只有 P1/P2，给出了虚假的 GO）
    code2, why2 = p39.verdict_of(0.9853, 0.0963, 0.9853, 0.9853, 0.9853)
    assert code2 == "NO_GO_NO_RECALL_GAIN" and "P0" in why2
    # P2 不满足（排序只比任取高 0.01 < 0.05）→ NO_GO
    code3, why3 = p39.verdict_of(0.97, 0.96, 0.97, 0.50, 0.90)
    assert code3 == "NO_GO" and "P2" in why3
    # P1 不满足（排序比 baseline 低出容差）→ NO_GO
    code4, why4 = p39.verdict_of(0.95, 0.10, 0.90, 0.50, 0.90)
    assert code4 == "NO_GO" and "P1" in why4


def test_preregistered_constants_are_explicit():
    p39 = _load()
    assert 0 < p39.P1_TOLERANCE < 0.05
    assert p39.P2_MIN_LIFT >= 0.01
    assert p39.TOP_K_FINAL == 5  # 与 settings.rag_rerank_output_top_k 对齐
    assert p39.RANDOM_SAMPLES >= 5  # 任取臂要多次采样，否则基线自身噪声太大


def test_placeholder_chunks_are_detected():
    """占位块（session_factory 没给）必须能被识别 —— 否则统计会静默算成全 0。"""
    from types import SimpleNamespace

    p39 = _load()
    placeholder = SimpleNamespace(
        chunk=SimpleNamespace(chunk_id="kb_v1.3.0:dialogue_内科_2553#0000",
                              source_doc_id="kb_v1.3.0:dialogue_内科_2553#0000",
                              content="[placeholder]")
    )
    real = SimpleNamespace(
        chunk=SimpleNamespace(chunk_id="kb_v1.3.0:dialogue_内科_2553#0000",
                              source_doc_id="dialogue_内科_2553",
                              content="真实内容")
    )
    assert p39.count_placeholder_chunks([placeholder, placeholder]) == 2
    assert p39.count_placeholder_chunks([real, real]) == 0
    assert p39.count_placeholder_chunks([placeholder, real]) == 1


def test_reuses_single_source_for_metrics_and_embeddings():
    src = (_AUDIT / "p39_graph_neighbor_ranking_sim.py").read_text(encoding="utf-8")
    assert "from gate2_bge_top10 import doc_level_hit_at_k" in src
    assert "from t1_eval_retrieval import cosine_topk, embed_texts" in src
    # 不许自造另外一把尺子
    assert "def doc_level_hit" not in src
    assert "def cosine" not in src
    assert "def auc" not in src
