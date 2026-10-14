"""A.63 批 WP2 守卫：answer_relevancy（RAGAS 官方口径）的 prompt / 判分 / 门控。

钉住四件事（每条都对应一个真会踩的坑）：

1. **prompt provenance**：`scripts/prompts/ragas_answer_relevancy.py` 必须写清
   来源仓库 / 文件路径 / 抓取 sha / 许可，并**保留官方原文关键句** ——
   否则半年后没人知道这段 prompt 从哪抄的、是不是被"顺手改写"过。
2. **官方判分公式**：`score = mean(cos(生成问题, 原始问题)) × int(not 全部 noncommittal)`
   —— 尤其"全 noncommittal 归零"这条，漏掉就会把敷衍回答判成高分。
3. **"没测到" ≠ "0 分"**：判官结构解析不出、嵌入服务不可用 → `score=None` + error，
   绝不给 0（否则门控会把仪器故障读成"不合格"）。
4. **门控开关语义**：`--skip-answer-relevancy` 是"本次不判"，与"报告里为 null（未测到）"
   在输出与 not_measured 语义上一致（都不判红），但不能互相冒充。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_JUDGE = _REPO / "scripts" / "langfuse" / "judge_eval_set.py"
_GATE = _REPO / "scripts" / "langfuse" / "eval_gate.py"
_PROMPT = _REPO / "scripts" / "prompts" / "ragas_answer_relevancy.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ----------------------------------------------------------------------
# 1) prompt provenance 与原文
# ----------------------------------------------------------------------


class TestPromptProvenance:
    def test_provenance_is_recorded(self):
        text = _PROMPT.read_text(encoding="utf-8")
        for needle in ("来源", "路径", "抓取", "许可", "Apache-2.0", "298b68274234c060deacab3cf5fb52aa3a20e885"):
            assert needle in text, f"provenance 缺 {needle}（来源不可考 = 不可审计）"
        assert "来源自证" in text, "必须写明'怎么确认抄的是同一份'"

    def test_official_instruction_is_verbatim(self):
        mod = _load(_PROMPT, "_ar_prompt_under_test")
        ins = mod.ANSWER_RELEVANCE_INSTRUCTION
        assert "Generate a question for the given answer" in ins
        assert "noncommittal" in ins
        # 官方默认 strictness=3；本仓库上限存在且 ≥ 默认
        assert mod.ANSWER_RELEVANCE_STRICTNESS == 3
        assert mod.ANSWER_RELEVANCE_MAX_STRICTNESS >= mod.ANSWER_RELEVANCE_STRICTNESS

    def test_official_examples_are_intact(self):
        mod = _load(_PROMPT, "_ar_prompt_under_test2")
        assert len(mod.ANSWER_RELEVANCE_EXAMPLES) == 3
        # 官方第三个例子就是 noncommittal=1 的形态（归零腿的依据）
        assert mod.ANSWER_RELEVANCE_EXAMPLES[-1][2] == 1
        assert all(len(ex) == 3 for ex in mod.ANSWER_RELEVANCE_EXAMPLES)


# ----------------------------------------------------------------------
# 2) 判分（余弦、官方公式、归零、不当作 0）
# ----------------------------------------------------------------------


class TestScoring:
    def test_cosine_pure_function(self):
        judge = _load(_JUDGE, "_ar_judge_under_test")
        assert judge._cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
        assert judge._cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
        assert judge._cosine([0.0, 0.0], [1.0, 0.0]) == 0.0  # 零范数
        assert judge._cosine([1.0], [1.0, 0.0]) == 0.0  # 维度不符

    def test_render_contains_instruction_and_examples(self):
        judge = _load(_JUDGE, "_ar_judge_under_test2")
        rendered = judge._render_answer_relevance_prompt("左叶低回声结节，边界清。")
        assert "Generate a question for the given answer" in rendered
        assert "左叶低回声结节，边界清。" in rendered
        assert '"noncommittal"' in rendered  # 示例以官方 JSON 形态给出

    def _patch(self, monkeypatch, judge, judge_returns, embed_returns):
        monkeypatch.setattr(judge, "_judge_chat", lambda *a, **k: judge_returns.pop(0))
        import t1_eval_retrieval

        monkeypatch.setattr(
            t1_eval_retrieval, "embed_texts", lambda texts, url, **k: embed_returns
        )

    def test_official_formula_is_mean_cosine(self, monkeypatch):
        judge = _load(_JUDGE, "_ar_judge_under_test3")
        replies = [
            json.dumps({"question": "q1", "noncommittal": 0}),
            json.dumps({"question": "q2", "noncommittal": 0}),
            json.dumps({"question": "q3", "noncommittal": 0}),
        ]
        # 问题向量 [1,0]；三个生成问题分别 [1,0] / [0,1] / [1,0] → cos = 1,0,1
        vecs = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 0.0]]
        self._patch(monkeypatch, judge, replies, vecs)
        out = judge._answer_relevancy_of(
            {"conclusion": "答案是甲", "clinical_text": "问：是什么？"}, "u", "m"
        )
        assert out["generated_questions"] == 3
        assert out["cosine_mean"] == 0.6667
        assert out["score"] == 0.6667
        assert out["all_noncommittal"] is False

    def test_all_noncommittal_scores_zero(self, monkeypatch):
        """官方公式的归零腿：生成的问题全部被判 noncommittal → 分数直接 0。"""
        judge = _load(_JUDGE, "_ar_judge_under_test4")
        replies = [
            json.dumps({"question": f"q{i}", "noncommittal": 1}) for i in range(3)
        ]
        vecs = [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]  # 余弦全 1.0
        self._patch(monkeypatch, judge, replies, vecs)
        out = judge._answer_relevancy_of(
            {"conclusion": "不知道", "clinical_text": "问：是什么？"}, "u", "m"
        )
        assert out["cosine_mean"] == 1.0
        assert out["all_noncommittal"] is True
        assert out["score"] == 0.0, "敷衍回答必须归零（官方 metric.py 的口径）"

    def test_unparsable_judge_output_is_not_zero(self, monkeypatch):
        judge = _load(_JUDGE, "_ar_judge_under_test5")
        self._patch(monkeypatch, judge, ["not json", "still not json", "nope"], [])
        out = judge._answer_relevancy_of(
            {"conclusion": "甲", "clinical_text": "问：是什么？"}, "u", "m"
        )
        assert out["score"] is None
        assert "parse_empty" in out["error"]

    def test_embedding_outage_is_not_zero(self, monkeypatch):
        judge = _load(_JUDGE, "_ar_judge_under_test6")
        replies = [json.dumps({"question": "q1", "noncommittal": 0})] * 3
        monkeypatch.setattr(judge, "_judge_chat", lambda *a, **k: replies.pop(0))
        import t1_eval_retrieval

        def _boom(*a, **k):
            raise RuntimeError("embedding down")

        monkeypatch.setattr(t1_eval_retrieval, "embed_texts", _boom)
        out = judge._answer_relevancy_of(
            {"conclusion": "甲", "clinical_text": "问：是什么？"}, "u", "m"
        )
        assert out["score"] is None
        assert "嵌入服务不可用" in out["error"]

    def test_missing_question_or_answer_is_reported(self):
        judge = _load(_JUDGE, "_ar_judge_under_test7")
        out = judge._answer_relevancy_of(
            {"conclusion": "甲", "clinical_text": ""}, "u", "m"
        )
        assert out["score"] is None and "clinical_text" in out["reason"]


# ----------------------------------------------------------------------
# 3) 门控：两项指标并列 + 开关
# ----------------------------------------------------------------------


def _gate_row(created_at: str) -> dict:
    return {
        "dedup_key": f"p|img-{created_at}",
        "patient_id": "p1",
        "request_id": f"req-{created_at}",
        "organ": "thyroid",
        "clinical_text": "甲状腺结节，实性低回声。",
        "conclusion": "甲状腺结节，建议随访。",
        "corrections": [],
        "created_at": created_at,
    }


def _write(tmp_path: Path, name: str, payload) -> Path:
    p = tmp_path / name
    if isinstance(payload, list):
        p.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in payload),
            encoding="utf-8",
        )
    else:
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _run_gate(monkeypatch, tmp_path: Path, *extra: str):
    gate = _load(_GATE, "_gate_for_ar_test")
    report = tmp_path / "gate_report.json"
    argv = [
        "eval_gate.py",
        "--eval-set",
        str(_write(tmp_path, "eval.jsonl", [_gate_row("2026-10-13T09:00:00+00:00")])),
        "--report",
        str(report),
        *extra,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    code = gate.main()
    body = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
    return code, body


def test_gate_reads_answer_relevance_as_number(monkeypatch, tmp_path):
    """判官报告里的 answer_relevance 是数字时，门控必须真的拿它判（并列两项指标）。"""
    judge_report = _write(
        tmp_path,
        "judge.json",
        {"faithfulness": 0.99, "answer_relevance": 0.93, "n": 1, "answer_mode": "raw"},
    )
    code, body = _run_gate(
        monkeypatch,
        tmp_path,
        "--judge-report",
        str(judge_report),
        "--judge-mode",
        "gate",
        "--enforce-since",
        "2026-10-13T00:00:00+00:00",
    )
    assert code == 0
    names = [c["name"] for c in body["enforced"]["checks"]]
    assert any("答案相关性" in n for n in names), "两项指标必须并列进门控"

    # 相关性掉到门限以下 → 必须判红（gate 模式）
    low = _write(
        tmp_path,
        "judge_low.json",
        {"faithfulness": 0.99, "answer_relevance": 0.42, "n": 1},
    )
    code2, body2 = _run_gate(
        monkeypatch,
        tmp_path,
        "--judge-report",
        str(low),
        "--judge-mode",
        "gate",
        "--enforce-since",
        "2026-10-13T00:00:00+00:00",
    )
    assert code2 == 1
    assert any(not c["ok"] and "答案相关性" in c["name"] for c in body2["enforced"]["checks"])


def test_gate_skip_flag_is_not_measured(monkeypatch, tmp_path):
    """显式 --skip-answer-relevancy：记 not_measured，低分也不判红。"""
    judge_report = _write(
        tmp_path,
        "judge.json",
        {"faithfulness": 0.99, "answer_relevance": 0.01, "n": 1},
    )
    code, body = _run_gate(
        monkeypatch,
        tmp_path,
        "--judge-report",
        str(judge_report),
        "--judge-mode",
        "gate",
        "--skip-answer-relevancy",
        "--enforce-since",
        "2026-10-13T00:00:00+00:00",
    )
    assert code == 0, "显式关闭的指标不许判红"
    assert "answer_relevance" in body["judge"]["metrics_not_measured"]
