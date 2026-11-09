"""评估门禁自身守卫（p115）—— 门禁自己坏了，比没门禁更危险。

## 为什么门禁需要被测试

门禁的作用是**拦住别人**。它自己算错时有两个失败方向：

1. **假绿**（该拦没拦）：报告里 NDCG 越界、n 分母不对，门禁说 ✅ —— 最危险；
2. **假红**（不该拦乱拦）：正常报告被拒，久而久之大家学会 `--no-verify` 绕过。

两个方向都必须有反证腿（**故意喂坏输入，确认它真的会红**）。
这正是本项目 `p28_degradation_visibility` 里"反证腿"的做法，这里照搬。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_GATE = _ROOT / "scripts/audit/p115_eval_gate.py"


def _run_gate(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_GATE), *args],
        capture_output=True, text=True, cwd=str(_ROOT),
    )


def _load_gate_module():
    """加载门禁模块。**必须先注册进 sys.modules**：

    门禁里有 `@dataclass`，dataclass 在解析字段类型时会
    `sys.modules.get(cls.__module__).__dict__`；模块没注册就拿到 None → `AttributeError`。
    这与 `p82_readability_golden_eval.load_checker` 里踩过的是同一个坑。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_p115_under_test", _GATE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ 正腿
def test_gate_passes_on_current_repo():
    """当前仓库必须全绿 —— 否则等于仓库里躺着已知的口径错误。"""
    r = _run_gate()
    assert r.returncode == 0, r.stdout
    assert "FAIL 0" in r.stdout


def test_gate_is_fast_enough_for_ci():
    """CI 门禁必须 <5s，否则会被塞进 nightly 从而失去约束力。"""
    import time

    t0 = time.monotonic()
    _run_gate()
    assert time.monotonic() - t0 < 5.0


# ------------------------------------------------------------------ 反证腿
class TestNegativeLegs:
    """故意造坏输入，确认门禁**真的会红**。"""

    def _write_report(self, tmp_path: pathlib.Path, mutate) -> pathlib.Path:
        base = json.loads((_ROOT / "reports/p114_layered.json").read_text(encoding="utf-8"))
        mutate(base)
        p = tmp_path / "report.json"
        p.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        return p

    def test_blocks_ndcg_out_of_range(self, tmp_path):
        def mutate(d):
            d["stages"]["retrieval"]["overall"]["ndcg@10"] = 1.23
        r = _run_gate("--report", str(self._write_report(tmp_path, mutate)))
        assert r.returncode == 1
        assert "ndcg@10" in r.stdout

    def test_blocks_missing_denominator_fields(self, tmp_path):
        def mutate(d):
            # 报告不自证分母 —— 2026-11-08 p94 的毛病
            d["stages"]["retrieval"]["overall"].pop("n_input")
            d["stages"]["retrieval"]["overall"].pop("duplicates_removed")
        r = _run_gate("--report", str(self._write_report(tmp_path, mutate)))
        assert r.returncode == 1
        assert "fields" in r.stdout

    def test_blocks_n_exceeding_n_input(self, tmp_path):
        def mutate(d):
            d["stages"]["retrieval"]["overall"]["n"] = 200
            d["stages"]["retrieval"]["overall"]["n_input"] = 117
        r = _run_gate("--report", str(self._write_report(tmp_path, mutate)))
        assert r.returncode == 1
        assert "n<=n_input" in r.stdout

    def test_blocks_regression_beyond_threshold(self, tmp_path):
        def mutate(d):
            d["stages"]["retrieval"]["overall"]["hit@10"] = 0.50
        r = _run_gate("--report", str(self._write_report(tmp_path, mutate)))
        assert r.returncode == 1
        assert "regress:hit@10" in r.stdout

    def test_blocks_shrunk_eval_set(self, tmp_path):
        """n 缩水比分数下降更危险：分母换了就没法横向比。"""

        def mutate(d):
            d["stages"]["retrieval"]["overall"]["n"] = 90
        r = _run_gate("--report", str(self._write_report(tmp_path, mutate)))
        assert r.returncode == 1
        assert "n_shrink" in r.stdout

    def test_blocks_missing_report_file(self, tmp_path):
        r = _run_gate("--report", str(tmp_path / "nope.json"))
        assert r.returncode == 1

    def test_tolerance_allows_small_drift(self, tmp_path):
        """2pt 阈值内的小抖动不该红（否则门禁会因浮点/随机性天天红）。"""
        base = json.loads((_ROOT / "reports/p114_layered.json").read_text(encoding="utf-8"))
        cur = base["stages"]["retrieval"]["overall"]["hit@10"]
        base["stages"]["retrieval"]["overall"]["hit@10"] = round(cur - 0.01, 6)
        p = tmp_path / "report.json"
        p.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        r = _run_gate("--report", str(p))
        assert r.returncode == 0, r.stdout


# ------------------------------------------------------------------ 口径检查的可测性
class TestCaliberChecks:
    def test_discovery_finds_the_known_implementations(self):
        """扫描必须真的扫到已知的实现 —— 扫不到就等于没在检查。"""
        gate = _load_gate_module()
        found = {rel for _, rel, _, _ in gate.discover_ndcg_implementations()}
        for must in (
            "app/evaluation/retrieval_metrics.py",       # 标准实现
            "app/observability/metrics_collector/rag_quality.py",  # 生产
            "scripts/audit/p94_b2_retrieval_eval.py",    # 审计 B2
            "scripts/audit/p94b_retrieval_ab.py",        # 审计 A/B
        ):
            assert must in found, f"扫描漏掉了 {must} —— 门禁会对它视而不见"

    def test_discovery_is_not_a_hardcoded_list(self, tmp_path, monkeypatch):
        """反证腿：往仓库里临时塞一个新实现，扫描必须立刻发现它。

        这条防的是"退回硬编码清单"——那样新增实现会静默逃过对拍，
        正是本项目 p94 修了 p94b 漏修的同一种病。
        """
        import shutil

        gate = _load_gate_module()
        before = {rel for _, rel, _, _ in gate.discover_ndcg_implementations()}
        probe = _ROOT / "scripts/audit/zz_probe_ndcg.py"
        probe.write_text(
            "import math\n\n\ndef ndcg_at_k(ranking, gold, k=10):\n"
            "    return 0.0\n",
            encoding="utf-8",
        )
        try:
            after = {rel for _, rel, _, _ in gate.discover_ndcg_implementations()}
            assert "scripts/audit/zz_probe_ndcg.py" in after, (
                "新加的 NDCG 实现没被扫描到 —— 说明又退回硬编码清单了"
            )
            assert after - before == {"scripts/audit/zz_probe_ndcg.py"}
        finally:
            probe.unlink()

    def test_injected_bug_is_caught(self, tmp_path, monkeypatch):
        """把 p94b 换成历史 bug 版，门禁必须红（端到端反证）。"""
        import shutil

        target = _ROOT / "scripts/audit/p94b_retrieval_ab.py"
        backup = tmp_path / "p94b.bak"
        shutil.copyfile(target, backup)
        orig = target.read_text(encoding="utf-8")
        import re

        buggy = '''def ndcg_at_k(ranking: list[str], gold: set[str], k: int = 10) -> float:
    """[PROBE] 复刻历史 bug：无去重、无 clamp。"""
    dcg = 0.0
    for rank, cid in enumerate(ranking[:k], start=1):
        if cid in gold:
            dcg += 1.0 / math.log2(rank + 1)
    n_ideal = min(len(gold), k)
    idcg = sum(1.0 / math.log2(r + 1) for r in range(1, n_ideal + 1))
    return dcg / idcg if idcg else 0.0


'''
        m = re.search(r"def ndcg_at_k.*?\n\n\n(?=# ---)", orig, re.S)
        assert m, "没找到可替换区间"
        target.write_text(orig[:m.start()] + buggy + orig[m.end():], encoding="utf-8")
        try:
            r = _run_gate()
            assert r.returncode == 1, "注入历史 bug 后门禁竟然还是绿的！"
            # 显示名是相对路径（扫描来的，不是硬编码别名）
            assert "p94b_retrieval_ab" in r.stdout
        finally:
            target.write_text(orig, encoding="utf-8")


# ------------------------------------------------------------------ 基线文件
def test_baseline_file_is_valid_and_complete():
    p = _ROOT / "data/eval/baselines/rag_eval_baseline.json"
    assert p.is_file(), "基线文件缺失，回归门禁形同虚设"
    d = json.loads(p.read_text(encoding="utf-8"))
    for key in ("n", "metrics", "frozen_at", "note"):
        assert key in d, f"基线缺 {key}"
    for metric in ("hit@1", "hit@5", "hit@10", "mrr@10", "ndcg@10"):
        assert metric in d["metrics"], f"基线缺指标 {metric}"
        assert 0.0 <= d["metrics"][metric] <= 1.0


def test_baseline_note_tells_how_to_update():
    """基线必须自带"什么时候才能更新"的说明，否则会被人随手改绿。"""
    p = _ROOT / "data/eval/baselines/rag_eval_baseline.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert "语料版本" in d["note"] or "确认" in d["note"]


@pytest.mark.parametrize("wrong", ["", "not json", "{}"])
def test_baseline_rejects_garbage(tmp_path, wrong):
    """基线损坏时必须报错而不是静默跳过（静默跳过 = 回归门禁永久失效）。"""
    bad = tmp_path / "base.json"
    bad.write_text(wrong, encoding="utf-8")
    r = _run_gate("--baseline", str(bad), "--report", "reports/p114_layered.json")
    # 空 JSON 缺字段 -> 至少不能是"全绿通过"
    assert r.returncode != 0 or "FAIL" in r.stdout or "WARN" in r.stdout
