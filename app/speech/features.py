"""Paraformer ONNX 前端特征管线（A.50，对齐 sherpa-onnx 前端实现）。

背景：适配器原先把 16kHz PCM 直接喂 `model.onnx` —— 但 Paraformer ONNX
（sherpa-onnx-paraformer-zh-2023-03-28，FunASR paraformer-large 的 CTC 导出，
词表 8404）的输入是 **560 维 LFR 堆叠 fbank 特征**，不是波形；且输出是
CTC logits 而非 token id。此前适配器从未对过真模型（探针 A.49 P9 记为
engine_ready=false 的部署缺口），本模块补齐前端：

    int16 波形（不除 32768，sherpa normalize_samples=false 同款尺度）
      → Kaldi 风格 fbank（25ms/10ms、80 维、hamming、snip_edges）
      → LFR 堆叠（window=7, shift=6，来自模型 metadata）
      → CMVN（neg_mean / inv_stddev，同样内嵌于模型 metadata）
      → [T, 560] float32

参考：sherpa-onnx/csrc/offline-recognizer-paraformer-impl.h（ApplyLFR→ApplyCMVN，
padding 用 0 而非 log(eps)）与 offline-paraformer-model.cc（metadata 键名
`lfr_window_size` / `lfr_window_shift` / `neg_mean` / `inv_stddev`）。

依赖纪律：只 import numpy/scipy —— 服务器 torch 由 vLLM 钉死版本，
torchaudio 未安装且**不允许为 ASR 动共享 venv**（硬规则 #3 同源纪律）。
"""
from __future__ import annotations

import math

import numpy as np

# Kaldi fbank 前端缺省（sherpa-onnx FeatureExtractor 同款）
_SAMPLE_RATE = 16000
_FRAME_LENGTH_MS = 25
_FRAME_SHIFT_MS = 10
_NUM_MEL_BINS = 80
_PREEMPH_COEF = 0.97
_LOW_FREQ = 20.0  # Kaldi 默认 low_freq；high_freq=0 表示取到 Nyquist


def _mel(freq):
    return 1127.0 * np.log1p(np.asarray(freq, dtype=np.float64) / 700.0)


def _inv_mel(m: float) -> float:
    return 700.0 * (math.exp(m / 1127.0) - 1.0)


def _build_mel_filterbank(n_fft: int, sr: int, num_bins: int) -> np.ndarray:
    """Kaldi 风格三角 mel 滤波器组（每行按和归一）。"""
    n_freqs = n_fft // 2 + 1
    freqs = np.linspace(0.0, sr / 2.0, n_freqs)
    mels = _mel(freqs)
    mel_low, mel_high = _mel(_LOW_FREQ), _mel(sr / 2.0)
    # Kaldi：bin 边界均匀切分，中心为 bin 1..num_bins（0 与 num_bins+1 为边界）
    edges = np.linspace(mel_low, mel_high, num_bins + 2)
    fbank = np.zeros((num_bins, n_freqs), dtype=np.float64)
    for b in range(num_bins):
        left, center, right = edges[b], edges[b + 1], edges[b + 2]
        up = (mels - left) / (center - left)
        down = (right - mels) / (right - center)
        weights = np.clip(np.minimum(up, down), 0.0, None)
        s = weights.sum()
        if s > 0:
            weights /= s  # Kaldi MelBanks 权重按行归一
        fbank[b] = weights
    return fbank


def kaldi_fbank(samples: np.ndarray, sr: int = _SAMPLE_RATE) -> np.ndarray:
    """Kaldi 风格 log-fbank。

    Args:
        samples: float32 一维波形，**保持 int16 量纲**（不除 32768，
            与 sherpa-onnx normalize_samples=false 一致）。
        sr: 采样率（模型要求 16000）。
    Returns:
        [T, 80] float32 log-mel 特征；样本不足一帧时返回 [0, 80]。
    """
    frame_len = int(sr * _FRAME_LENGTH_MS / 1000)
    frame_shift = int(sr * _FRAME_SHIFT_MS / 1000)
    n = samples.shape[0]
    if n < frame_len:
        return np.zeros((0, _NUM_MEL_BINS), dtype=np.float32)

    num_frames = 1 + (n - frame_len) // frame_shift  # snip_edges=true
    idx = np.arange(frame_len)[None, :] + frame_shift * np.arange(num_frames)[:, None]
    frames = samples[idx].astype(np.float64)

    frames -= frames.mean(axis=1, keepdims=True)  # remove_dc_offset
    frames[:, 1:] -= _PREEMPH_COEF * frames[:, :-1]
    frames[:, 0] *= 1.0 - _PREEMPH_COEF  # Kaldi 预加重含首样本

    window = 0.54 - 0.46 * np.cos(
        2.0 * np.pi * np.arange(frame_len) / (frame_len - 1)
    )  # hamming（Kaldi 公式）
    frames *= window[None, :]

    n_fft = 1
    while n_fft < frame_len:
        n_fft <<= 1
    spec = np.abs(np.fft.rfft(frames, n=n_fft, axis=1)) ** 2  # 功率谱

    mel_fb = _build_mel_filterbank(n_fft, sr, _NUM_MEL_BINS)
    feat = spec @ mel_fb.T
    feat = np.log(np.maximum(feat, np.finfo(np.float32).eps))
    return feat.astype(np.float32)


def apply_lfr(feat: np.ndarray, window: int, shift: int) -> np.ndarray:
    """低帧率堆叠：每 shift 帧取 window 帧拼接，尾部用最后一帧补齐。

    对齐 sherpa-onnx csrc/lfr.cc：输出帧数 = ceil(T / shift)，
    越界窗口以最后一帧重复填充（padding 用 0，不用 log(eps)）。
    """
    if window <= 0 or shift <= 0:
        raise ValueError(f"invalid LFR params: window={window}, shift={shift}")
    t = feat.shape[0]
    out_len = math.ceil(t / shift) if t > 0 else 0
    if out_len == 0:
        return feat.reshape(0, feat.shape[1] * window)
    need = (out_len - 1) * shift + window
    if need > t:
        pad = np.repeat(feat[-1:], need - t, axis=0)
        feat = np.vstack([feat, pad])
    stacked = np.concatenate(
        [feat[i : i + window].reshape(1, -1) for i in range(0, out_len * shift, shift)],
        axis=0,
    )
    return stacked.astype(np.float32)


def apply_cmvn(feat: np.ndarray, neg_mean: np.ndarray, inv_stddev: np.ndarray) -> np.ndarray:
    """CMVN：`(feat + neg_mean) * inv_stddev`（sherpa 同款参数语义）。"""
    if feat.shape[1] != neg_mean.shape[0] or feat.shape[1] != inv_stddev.shape[0]:
        raise ValueError(
            f"CMVN dim mismatch: feat={feat.shape[1]}, "
            f"neg_mean={neg_mean.shape[0]}, inv_stddev={inv_stddev.shape[0]}"
        )
    return ((feat + neg_mean[None, :]) * inv_stddev[None, :]).astype(np.float32)


class ParaformerFrontend:
    """组合前端：波形 → LFR 堆叠 fbank → CMVN → [T, 560]。

    参数来自模型 ONNX metadata（sherpa-onnx 同源键名），见 funasr.py 的读取处。
    """

    def __init__(self, lfr_window_size: int, lfr_window_shift: int,
                 neg_mean: np.ndarray, inv_stddev: np.ndarray):
        self.lfr_window_size = lfr_window_size
        self.lfr_window_shift = lfr_window_shift
        self.neg_mean = neg_mean
        self.inv_stddev = inv_stddev

    @classmethod
    def from_metadata(cls, meta: dict[str, str]) -> ParaformerFrontend:
        """从 ONNX custom_metadata_map 构造（键名对齐 sherpa-onnx）。"""
        try:
            lfr_m = int(meta["lfr_window_size"])
            lfr_n = int(meta["lfr_window_shift"])
            neg_mean = cls._parse_vec(meta["neg_mean"])
            inv_stddev = cls._parse_vec(meta["inv_stddev"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"paraformer metadata 缺失或非法: {exc}") from exc
        return cls(lfr_m, lfr_n, neg_mean, inv_stddev)

    @staticmethod
    def _parse_vec(raw: str) -> np.ndarray:
        cleaned = raw.replace("[", " ").replace("]", " ").replace(",", " ")
        return np.array([float(x) for x in cleaned.split()], dtype=np.float32)

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        """int16 量纲 float 波形 → [T, lfr_window_size*80] float32 特征。"""
        feat = kaldi_fbank(samples)
        feat = apply_lfr(feat, self.lfr_window_size, self.lfr_window_shift)
        return apply_cmvn(feat, self.neg_mean, self.inv_stddev)


def ctc_greedy_decode(logits: np.ndarray, blank_id: int = 0) -> list[int]:
    """CTC 贪心解码：逐帧 argmax → 折叠连续重复 → 去 blank。

    Args:
        logits: [T, vocab] float。
    Returns:
        token id 序列。
    """
    ids = np.argmax(logits, axis=-1).tolist()
    out: list[int] = []
    prev: int | None = None
    for i in ids:
        if i != prev and i != blank_id:
            out.append(int(i))
        prev = int(i)
    return out
