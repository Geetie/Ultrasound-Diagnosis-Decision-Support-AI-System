"""属性提示引擎单测（不加载真实 ULC 权重，用 fake text tower 验证逻辑）。

覆盖：维度白名单、prompt 句式（论文附录 F.2）、一致性比对、禁用维度说明、
缺权重/关开关降级、health 不抛、参数校验。
"""
from __future__ import annotations

import pytest

from app.inference.attribute_hints import (
    DISABLED_REASON,
    LABELS,
    TASKS,
    AttributeHintEngine,
    AttributeHintsUnavailableError,
)

pytestmark = [pytest.mark.track_inf]


@pytest.fixture
def fake_engine(monkeypatch):
    """注入 fake 塔：让 prompts 里含特定词的维度判定为固定标签。"""
    import torch

    eng = AttributeHintEngine.get()

    class _FakeTower:
        def __call__(self, toks):
            # toks 是 HF tokenizer 输出；用长度伪造一个可区分的向量
            n = toks.shape[0]
            out = torch.zeros(n, 512)
            out[:, 0] = 1.0  # 默认全等
            return out

    eng._text_tower = _FakeTower()
    eng._tokenizer = lambda prompts: torch.zeros(len(prompts), 8, dtype=torch.long)
    eng._device = "cpu"
    # prompt 特征：512 维 one-hot（第 i 类在第 i 位），配合 fake 塔（首位=1）
    # → 每维 argmax 恒为第 0 类，逻辑可确定断言
    eng._prompt_feats = {
        f: torch.eye(len(LABELS[f]), 512) for f, _z, _l, on in TASKS if on
    }
    eng._meta = {"weights": "fake"}
    yield eng
    AttributeHintEngine._instance = None


class TestTaskConfig:
    def test_enabled_dims_are_six(self):
        enabled = [f for f, _z, _l, on in TASKS if on]
        assert len(enabled) == 6
        assert "Vascularity" not in enabled
        assert "InternalCharacteristics" not in enabled
        assert "PosteriorAcoustics" not in enabled

    def test_disabled_dims_have_reason(self):
        for f in ("Vascularity", "InternalCharacteristics", "PosteriorAcoustics"):
            assert f in DISABLED_REASON
            assert DISABLED_REASON[f]

    def test_labels_nonempty_for_all_tasks(self):
        for f, _z, _l, _on in TASKS:
            assert LABELS[f], f


class TestHints:
    def test_returns_six_dims(self, fake_engine):
        r = fake_engine.hints_for_text("甲状腺内低回声结节，边界清，形态规则")
        assert len(r.hints) == 6
        assert r.device == "cpu"
        assert r.text_length > 0
        d = r.as_dict()
        assert set(d) >= {"hints", "disabled", "conflicts", "device"}
        assert len(d["disabled"]) == 3

    def test_conflicts_counted(self, fake_engine):
        r = fake_engine.hints_for_text("边界清的低回声结节")
        # 报告声明与 fake 判定（argmax 恒为第 0 类）可能一致或冲突，
        # 关键是有 agree 字段且计数自洽
        claims = [h for h in r.hints if h.report_claim is not None]
        expected = sum(1 for h in claims if h.agree is False)
        assert r.conflicts == expected

    def test_no_claim_returns_none_agree(self, fake_engine):
        r = fake_engine.hints_for_text("甲状腺结节")
        for h in r.hints:
            if h.report_claim is None:
                assert h.agree is None

    def test_empty_text_raises(self, fake_engine):
        with pytest.raises(ValueError):
            fake_engine.hints_for_text("")

    def test_confidence_and_margin_bounded(self, fake_engine):
        r = fake_engine.hints_for_text("低回声")
        for h in r.hints:
            assert -1.0 <= h.confidence <= 1.0
            assert h.margin >= -1.0


class TestClaimExtraction:
    def test_maps_chinese_phrases(self):
        f = AttributeHintEngine._claim_from_text
        assert f("Margins", "边界清晰") == "well-defined"
        assert f("Margins", "边界欠清") == "ill-defined/indistinct"
        assert f("Echogenicity", "低回声区") == "hypoechoic"
        assert f("Shape", "椭圆形") == "oval"
        assert f("Diagnosis", "考虑结节") == "nodule"

    def test_returns_none_when_absent(self):
        f = AttributeHintEngine._claim_from_text
        assert f("Margins", "甲状腺结节") is None
        # 血流维度没有 claim 映射
        assert f("Vascularity", "血流丰富") is None


class TestDegradation:
    def test_missing_weights_raises(self, monkeypatch):
        from pathlib import Path

        monkeypatch.setattr(
            "app.inference.attribute_hints._clip_weights",
            lambda: Path("/nonexistent/checkpoints.pt"),
        )
        eng = AttributeHintEngine.get()
        eng._text_tower = None
        with pytest.raises(AttributeHintsUnavailableError, match="权重不存在"):
            eng._ensure_loaded()
        AttributeHintEngine._instance = None

    def test_disabled_switch_raises(self, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "attribute_hints_enabled", False, raising=False)
        eng = AttributeHintEngine.get()
        eng._text_tower = None
        with pytest.raises(AttributeHintsUnavailableError, match="已关闭"):
            eng._ensure_loaded()
        AttributeHintEngine._instance = None

    def test_health_reports_unavailable(self, monkeypatch):
        from pathlib import Path

        monkeypatch.setattr(
            "app.inference.attribute_hints._clip_weights",
            lambda: Path("/nonexistent/checkpoints.pt"),
        )
        eng = AttributeHintEngine.get()
        eng._text_tower = None
        h = eng.health()
        assert h["available"] is False
        AttributeHintEngine._instance = None

    def test_health_when_loaded(self, fake_engine):
        h = fake_engine.health()
        assert h["available"] is True
        assert len(h["enabled_dims"]) == 6
        assert len(h["disabled_dims"]) == 3
