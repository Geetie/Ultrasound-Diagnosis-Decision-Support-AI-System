"""A.50：Paraformer ONNX 前端特征管线与适配器分流。

背景：适配器原先把 16kHz PCM 直接喂 model.onnx——Paraformer ONNX 的输入
是 LFR 堆叠 fbank（[T,560]），输出是 CTC logits，直喂波形 100% 失败
（真机 A.49 P9 记 engine_ready=false 部署缺口，A.50 补齐管线）。

本文件全部走纯 numpy / 假 session，不加载真模型（真模型功能验证在
scripts/audit/validate_asr.py，服务器真机执行）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.speech.features import (
    ParaformerFrontend,
    apply_cmvn,
    apply_lfr,
    ctc_greedy_decode,
    kaldi_fbank,
)
from app.speech.funasr import FunASROnnxEngine
from app.speech.protocols import SpeechRequest

pytestmark = [pytest.mark.track_edg, pytest.mark.track_llm]


class TestKaldiFbank:
    def test_shape_and_finite(self):
        x = np.random.randn(16000).astype(np.float32)  # 1s
        feat = kaldi_fbank(x)
        # 25ms 窗/10ms 移位，snip_edges：1 + (16000-400)//160 = 98
        assert feat.shape == (98, 80)
        assert np.isfinite(feat).all()

    def test_short_samples_return_empty(self):
        feat = kaldi_fbank(np.zeros(100, dtype=np.float32))
        assert feat.shape[0] == 0

    def test_log_scale_output(self):
        """fbank 是对数域：值应远小于线性域能量量级。"""
        feat = kaldi_fbank(np.random.randn(16000).astype(np.float32))
        assert feat.max() < 100.0


class TestLfr:
    def test_shape_and_content(self):
        feat = np.arange(12, dtype=np.float32).reshape(6, 2)
        out = apply_lfr(feat, window=7, shift=6)
        # 6 帧、shift=6 → 1 个窗口；窗口需 7 帧 → 尾部补最后一帧
        assert out.shape == (1, 7 * 2)
        assert out[0, :2].tolist() == [0.0, 1.0]
        assert out[0, -2:].tolist() == [10.0, 11.0]  # 补齐帧 = 最后一帧

    def test_multi_window_stride(self):
        feat = np.random.randn(20, 80).astype(np.float32)
        out = apply_lfr(feat, window=7, shift=6)
        assert out.shape == (4, 560)  # ceil(20/6)=4
        np.testing.assert_array_equal(out[0], feat[0:7].reshape(-1))

    def test_invalid_params_rejected(self):
        with pytest.raises(ValueError):
            apply_lfr(np.zeros((3, 2), dtype=np.float32), window=0, shift=6)


class TestCmvn:
    def test_formula(self):
        feat = np.ones((2, 3), dtype=np.float32)
        out = apply_cmvn(feat, neg_mean=np.array([-1.0, -2.0, -3.0]),
                         inv_stddev=np.array([2.0, 2.0, 2.0]))
        np.testing.assert_allclose(out[0], [0.0, -2.0, -4.0])

    def test_dim_mismatch_rejected(self):
        with pytest.raises(ValueError):
            apply_cmvn(np.zeros((2, 4), dtype=np.float32),
                       neg_mean=np.zeros(3, dtype=np.float32),
                       inv_stddev=np.zeros(3, dtype=np.float32))


class TestParaformerFrontend:
    def test_from_metadata_parses_sherpa_style(self):
        fe = ParaformerFrontend.from_metadata({
            "lfr_window_size": "7",
            "lfr_window_shift": "6",
            "neg_mean": " ".join(["-1.5"] * 560),
            "inv_stddev": " ".join(["0.5"] * 560),
        })
        assert fe.lfr_window_size == 7 and fe.lfr_window_shift == 6
        assert fe.neg_mean.shape == (560,)

    def test_from_metadata_missing_keys_rejected(self):
        with pytest.raises(ValueError, match="metadata"):
            ParaformerFrontend.from_metadata({"lfr_window_size": "7"})

    def test_end_to_end_1s_audio(self):
        fe = ParaformerFrontend(7, 6, np.zeros(560, dtype=np.float32),
                                np.ones(560, dtype=np.float32))
        out = fe(np.random.randn(16000).astype(np.float32))
        assert out.shape == (17, 560)  # ceil(98/6)
        assert np.isfinite(out).all()


class TestCtcGreedyDecode:
    def test_collapse_and_blank_removal(self):
        logits = np.array([
            [0.1, 0.9],  # 1
            [0.9, 0.1],  # 0 (blank)
            [0.9, 0.1],  # 0 (blank，连续重复)
            [0.1, 0.9],  # 1（与前一非 blank 不连续 → 保留）
        ])
        assert ctc_greedy_decode(logits, blank_id=0) == [1, 1]

    def test_repeated_non_blank_collapsed(self):
        logits = np.array([[0.1, 0.9], [0.1, 0.9], [0.1, 0.9]])
        assert ctc_greedy_decode(logits) == [1]

    def test_all_blank(self):
        logits = np.tile(np.array([1.0, 0.0]), (5, 1))
        assert ctc_greedy_decode(logits) == []


# ---------------------------------------------------------------- 适配器分流


class _FakeMeta:
    def __init__(self, meta: dict[str, str]):
        self.custom_metadata_map = meta


class _FakeInput:
    def __init__(self, name: str):
        self.name = name


class _FakeSession:
    """最小 ORT InferenceSession 替身：记录 feeds，返回固定 logits。"""

    def __init__(self, meta: dict[str, str], vocab: int = 4, frames: int = 3):
        self._meta = meta
        self._vocab = vocab
        self.frames = frames
        self.get_inputs = lambda: [_FakeInput("speech"), _FakeInput("speech_lengths")]
        self.get_outputs = lambda: [_FakeInput("logits")]
        self.get_modelmeta = lambda: _FakeMeta(meta)
        self.run = lambda names, feeds: self._run(feeds)

    def _run(self, feeds):
        t = self.frames
        logits = np.zeros((1, t, self._vocab), dtype=np.float32)
        logits[0, :, 1] = 1.0  # 每帧 argmax=1
        return [logits, np.array([[t]], dtype=np.int64)]


def _request(seconds: float = 0.2) -> SpeechRequest:
    n = int(16000 * seconds)
    pcm = (np.zeros(n, dtype=np.int16)).tobytes()
    # 直接给 bytes（端点层才做 base64 解码；pydantic v2 对 str 会按 utf-8 编码）
    return SpeechRequest(audio_data=pcm, sample_rate=16000)


def _meta() -> dict[str, str]:
    return {
        "lfr_window_size": "7",
        "lfr_window_shift": "6",
        "neg_mean": " ".join(["-1.5"] * 560),
        "inv_stddev": " ".join(["0.5"] * 560),
        "vocab_size": "8404",
    }


class TestEngineParaformerPath:
    @pytest.fixture
    def dummy_model_dir(self, tmp_path):
        """哑模型目录：绕过 initialize 的文件存在性检查（session 用假对象）。"""
        d = tmp_path / "funasr"
        d.mkdir()
        (d / "model.onnx").write_bytes(b"\x00")
        (d / "tokens.txt").write_text("<blank>\n甲\n乙\n", encoding="utf-8")
        return str(d)

    @pytest.mark.asyncio
    async def test_recognize_uses_feature_pipeline(self, monkeypatch, dummy_model_dir):
        """有 metadata → 走特征管线：喂给 session 的是 [1,T,560] 特征而非波形。"""
        engine = FunASROnnxEngine(model_dir=dummy_model_dir)
        seen: dict = {}

        class _PipeSession:
            get_inputs = staticmethod(lambda: [_FakeInput("speech"), _FakeInput("speech_lengths")])
            get_outputs = staticmethod(lambda: [_FakeInput("logits")])
            get_modelmeta = staticmethod(lambda: _FakeMeta(_meta()))

            @staticmethod
            def run(_names, feeds):
                seen.update(feeds)
                logits = np.zeros((1, 3, 4), dtype=np.float32)
                logits[0, :, 1] = 1.0  # 每帧 argmax=1 → token "甲"
                return [logits, np.array([[3]], dtype=np.int64)]

        monkeypatch.setattr("onnxruntime.InferenceSession", lambda *a, **k: _PipeSession())
        await engine.initialize()
        assert engine._frontend is not None, "metadata 齐全时必须启用 paraformer 前端"

        resp = await engine.recognize(_request())
        speech = seen["speech"]
        # 超短输入被零填充到 1s=16000 采样 → 98 fbank 帧 → ceil(98/6)=17 窗
        assert speech.shape == (1, 17, 560)
        assert seen["speech_lengths"].tolist() == [17]
        assert resp.text == "甲"  # 假 session 固定 3 帧全 argmax=1 → CTC 折叠 1 个
        assert resp.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_recognize_without_metadata_falls_back(self, monkeypatch, dummy_model_dir):
        """metadata 缺键 → 旧波形路径（不崩，可出空文本）。"""
        engine = FunASROnnxEngine(model_dir=dummy_model_dir)

        class _LegacySession:
            get_inputs = staticmethod(lambda: [_FakeInput("audio"), _FakeInput("length")])
            get_outputs = staticmethod(lambda: [_FakeInput("out")])
            get_modelmeta = staticmethod(lambda: _FakeMeta({}))

            @staticmethod
            def run(_names, feeds):
                waveform = next(iter(feeds.values()))
                assert waveform.ndim == 2, "旧路径应喂 [1, N] 归一化波形"
                # 旧路径语义：输出是 token-id 数组（ys_pad），直接查 tokenizer
                return [np.array([[2, 1]], dtype=np.int64)]

        monkeypatch.setattr("onnxruntime.InferenceSession", lambda *a, **k: _LegacySession())
        await engine.initialize()
        assert engine._frontend is None
        resp = await engine.recognize(_request())
        # 哑 tokens.txt: 0=<blank> 1=甲 2=乙 → ids [2,1] → "乙甲"
        assert resp.text == "乙甲"
