"""`scripts/langfuse/eval_gate.py` 的两层门控语义守卫（A.61）。

为什么值得钉住（都是真机首跑踩出来的）：
1. **存量 vs 近端**：评测集是累积资产，含可读化之前的旧判读（实测 118/142 带技术串）。
   若存量也算进新账，门禁**永远红** → 棘轮失效。守卫要求：近端干净 → 退出 0，存量仅告知。
2. **近端为空时不许"默认通过"**：报告必须写 `no_recent_samples`（没样本 ≠ 达标）。
3. **L2 部分判官报告**：官方 `answer_relevancy` prompt 本仓库未收录 →
   `answer_relevance: null` 必须记 `not_measured`，**不能按 0.0 判红**（"没测"≠"不及格"）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_GATE = Path(__file__).resolve().parents[3] / "scripts" / "langfuse" / "eval_gate.py"

#: 近端/存量的分界（与脚本默认一致；有技术串的旧样本）
_OLD = "2026-10-01T00:00:00+00:00"
_NEW = "2026-10-13T09:00:00+00:00"


def _load_gate():
    spec = importlib.util.spec_from_file_location("_eval_gate_under_test", _GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(created_at: str, conclusion: str, clinical_text: str = "甲状腺结节，实性低回声。") -> dict:
    return {
        "dedup_key": f"p|img-{created_at}",
        "patient_id": "p1",
        "request_id": f"req-{created_at}",
        "organ": "thyroid",
        "clinical_text": clinical_text,
        "conclusion": conclusion,
        "corrections": [],
        "created_at": created_at,
    }


def _write_eval_set(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "eval_set.jsonl"
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
    )
    return p


def _run(monkeypatch, eval_set: Path, tmp_path: Path, *extra: str) -> tuple[int, dict]:
    gate = _load_gate()
    report = tmp_path / "gate_report.json"
    argv = [
        "eval_gate.py",
        "--eval-set",
        str(eval_set),
        "--report",
        str(report),
        *extra,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    code = gate.main()
    body = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
    return code, body


def test_存量泄漏不判红_近端干净即通过(monkeypatch, tmp_path):
    """棘轮口径：旧样本（A.59 之前）带技术串 → 只告知；新样本干净 → 退出 0。"""
    rows = [
        _row(_OLD, "[thyroid] grade_unparseable: LLM output did not contain a valid grade"),
        _row(_NEW, "甲状腺 · TR3 — 分级: TR3 总分: 4"),
    ]
    code, report = _run(monkeypatch, _write_eval_set(tmp_path, rows), tmp_path)

    assert code == 0, "近端干净就必须通过，不能因存量判红"
    assert report["enforced"]["n"] == 1 and report["passed"] is True
    assert report["informational"]["n"] == 2
    # 存量泄漏必须**可见**（告知，不吞）
    g2_all = next(c for c in report["informational"]["checks"] if c["name"].startswith("G2"))
    assert g2_all["ok"] is False


def test_近端为空记no_recent_samples而非默认达标(monkeypatch, tmp_path):
    code, report = _run(monkeypatch, _write_eval_set(tmp_path, [_row(_OLD, "甲状腺 · TR3")]), tmp_path)

    assert code == 0
    assert report["enforced"]["status"] == "no_recent_samples"
    assert report["enforced"]["checks"] == []


def test_近端技术串泄漏判红(monkeypatch, tmp_path):
    rows = [_row(_NEW, "[cardiac] LVEF=unknown, severity=normal — 结论")]
    code, report = _run(monkeypatch, _write_eval_set(tmp_path, rows), tmp_path)

    assert code == 1
    g2 = next(c for c in report["enforced"]["checks"] if c["name"].startswith("G2"))
    assert g2["ok"] is False


def test_L2部分判官报告_缺相关性记not_measured不判红(monkeypatch, tmp_path):
    """官方相关性 prompt 未收录 → 只对 faithfulness 加判据，缺的字段如实登记。"""
    judge = tmp_path / "judge.json"
    judge.write_text(
        json.dumps(
            {
                "engine": "ragas_faithfulness(official prompts)",
                "n": 20,
                "faithfulness": 0.97,
                "answer_relevance": None,
                "skipped_no_context": 4,
            }
        ),
        encoding="utf-8",
    )
    rows = [_row(_NEW, "甲状腺 · TR3 — 分级: TR3 总分: 4")]
    code, report = _run(
        monkeypatch, _write_eval_set(tmp_path, rows), tmp_path, "--judge-report", str(judge)
    )

    assert code == 0, "相关性缺失不得按 0 判红"
    assert report["judge"]["status"] == "ran"
    assert report["judge"]["metrics_not_measured"] == ["answer_relevance"]
    l2 = [c for c in report["advisory"] if c["name"].startswith("L2")]
    assert [c["name"] for c in l2] == ["L2 faithfulness ≥ 0.95"]
    assert l2[0]["ok"] is True


def test_L2默认advisory_不达标仍显式可见但不判红(monkeypatch, tmp_path):
    """口径未拍板时：L2 照常测量并展示 `ok=false`，但**不决定退出码**（避免红灯噪声）。

    关键：`ok=false` 必须原样写进报告 —— advisory 不等于"当作通过"。
    """
    judge = tmp_path / "judge.json"
    judge.write_text(json.dumps({"n": 12, "faithfulness": 0.08}), encoding="utf-8")
    rows = [_row(_NEW, "甲状腺 · TR3 — 分级: TR3 总分: 4")]
    code, report = _run(
        monkeypatch, _write_eval_set(tmp_path, rows), tmp_path, "--judge-report", str(judge)
    )

    assert code == 0, "advisory 不参与退出码"
    assert report["judge"]["enforcement"] == "advisory"
    assert report["judge"]["faithfulness"] == 0.08
    l2 = next(c for c in report["advisory"] if c["name"].startswith("L2 faithfulness"))
    assert l2["ok"] is False, "低分必须显式记 ok=false（不许被 advisory 洗成通过）"
    assert [c for c in report["enforced"]["checks"] if c["name"].startswith("L2")] == []


def test_L2_gate模式_不达标判红(monkeypatch, tmp_path):
    """口径拍板后切 `--judge-mode gate`：L2 进入退出码。"""
    judge = tmp_path / "judge.json"
    judge.write_text(json.dumps({"n": 12, "faithfulness": 0.08}), encoding="utf-8")
    rows = [_row(_NEW, "甲状腺 · TR3 — 分级: TR3 总分: 4")]
    code, report = _run(
        monkeypatch,
        _write_eval_set(tmp_path, rows),
        tmp_path,
        "--judge-report",
        str(judge),
        "--judge-mode",
        "gate",
    )

    assert code == 1
    l2 = next(c for c in report["enforced"]["checks"] if c["name"].startswith("L2 faithfulness"))
    assert l2["ok"] is False


def test_缺评测集返回2(monkeypatch, tmp_path):
    code, _ = _run(monkeypatch, tmp_path / "missing.jsonl", tmp_path)
    assert code == 2


def test_gate切换判据必须是明说的四条() -> None:
    """`--judge-mode gate` 的开启条件必须是**明说判据**，不是"感觉可以了"（21 号批 1 要求）。

    A.62 批 1 尾段的教训：台账里写着"第三种口径"，代码里根本没有该模式；口径没拍板就把
    L2 挂上退出码＝红灯噪声。故把切换条件钉在脚本里（口径拍板 / n ≥ 30 / 门限语义 /
    证据链落地），并连三口径实测数字一起写在同一处 —— 谁想切，先看见代价。
    """
    src = _GATE.read_text(encoding="utf-8")
    block = src[src.index('"--judge-mode"') : src.index('"--enforce-since"')]
    for must in ("拍板", "≥ 30", "门限", "证据链", "advisory", "gate"):
        assert must in block, f"`--judge-mode` 的切换判据缺「{must}」——开启条件必须是明说的"
    assert "A=判全文" in block and "C=剔除派生字段" in block, (
        "三口径实测数字必须写在切换判据旁边（否则口径拍板又变成靠感觉）"
    )


def test_可指定斩断点_纳入旧样本即判红(monkeypatch, tmp_path):
    """生产复跑要能传本次部署时间戳；把旧样本纳入近端就该判红（口径可指定、有效）。"""
    rows = [_row(_OLD, "[thyroid] 旧格式"), _row(_NEW, "甲状腺 · TR3 — 分级: TR3 总分: 4")]
    eval_set = _write_eval_set(tmp_path, rows)

    # ① 斩断点卡在旧样本之后 → 近端干净 → 通过
    code_clean, report_clean = _run(
        monkeypatch, eval_set, tmp_path, "--enforce-since", "2026-10-13T00:00:00"
    )
    assert code_clean == 0 and report_clean["enforced"]["n"] == 1

    # ② 斩断点提前到旧样本之前 → 旧样本进近端 → 泄漏判红
    code_dirty, report_dirty = _run(
        monkeypatch, eval_set, tmp_path, "--enforce-since", "2026-09-01T00:00:00"
    )
    assert code_dirty == 1 and report_dirty["enforced"]["n"] == 2
    assert report_dirty["enforce_since"] == "2026-09-01T00:00:00"
