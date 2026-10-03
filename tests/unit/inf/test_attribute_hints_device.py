"""属性提示的**设备一致性**回归（2026-10-04 真机 GPU 路径事故）。

事故：prompt 特征缓存为了省显存存在 CPU，而编码结果在 tower 所在设备；
GPU 路径下 `pf @ tf` 抛 "Expected all tensors to be on the same device"。
CPU 路径永远复现不出来（都是 CPU），所以必须用假设备钉死语义。

本测试不依赖真 GPU：用 fake device 字符串 + 假张量替身，
断言"相似度计算时两个张量在同一设备"。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_inf]

from app.inference.attribute_hints import AttributeHintEngine


class _FakeTensor:
    """记录 .to(device) 调用的小张量替身。"""

    def __init__(self, tag: str, log: list[tuple[str, str]]):
        self.tag = tag
        self._log = log
        self.device = "cpu"

    def to(self, device):
        self._log.append((self.tag, device))
        self.device = device
        return self

    def float(self):
        return self

    def __getitem__(self, idx):
        return self

    def __matmul__(self, other):
        if self.device != other.device:
            raise AssertionError(
                f"device mismatch: {self.tag}@{self.device} vs {other.tag}@{other.device}"
            )
        return _FakeScores(self.device)

    def __radd__(self, other):  # 兼容 sum()/len() 之类
        return self


class _FakeScores:
    def __init__(self, device: str):
        self.device = device
        self._vals = [0.9, 0.4]

    def tolist(self):
        return list(self._vals)


class _FakeTower:
    def __init__(self, log):
        self._log = log

    def __call__(self, toks):
        return _FakeTensor("tower_out", self._log)


class _FakeTokenizer:
    def __call__(self, texts):
        return _FakeTensor("tokenizer_out", [])

    def to(self, device):
        return self


class _Engine(AttributeHintEngine):
    """绕开真实装载，只测设备一致性那一段。"""

    def __init__(self, device: str, log: list[tuple[str, str]]):
        self._device = device
        self._log = log
        self._text_tower = _FakeTower(log)
        self._tokenizer = _FakeTokenizer()
        self._meta = {"tower": "fake", "official_zero_shot_macro_acc": 0.6894}
        # 6 个启用维度各放一张"缓存"张量
        from app.inference.attribute_hints import TASKS

        self._prompt_feats = {
            name: _FakeTensor(f"cache_{name}", log)
            for name, _zh, _l, on in TASKS
            if on
        }

    def _ensure_loaded(self):
        return None

    def detect_language(self, text):
        return "en"


class TestDeviceConsistency:
    def test_cuda_path_does_not_raise(self, monkeypatch):
        """GPU 路径：缓存（CPU）与编码结果（cuda）必须在同一设备上运算。"""
        log: list[tuple[str, str]] = []
        eng = _Engine("cuda", log)

        # 让"编码结果"落在 cuda 上，模拟 tower 在 GPU
        real_normalize = None
        import torch.nn.functional as F

        def fake_normalize(t, dim=-1):
            t = t.to(eng._device)  # 模拟 tower 输出在 cuda
            return t

        monkeypatch.setattr(F, "normalize", fake_normalize)
        assert real_normalize is None  # 仅表明没用到真实实现

        result = eng.hints_for_text("hypoechoic nodule")
        assert result is not None
        # 每个启用维度的缓存都被搬到了 cuda
        moved = [d for t, d in log if t.startswith("cache_")]
        assert moved, "prompt 缓存应被搬到推理设备"
        assert set(moved) == {"cuda"}

    def test_cpu_path_keeps_everything_on_cpu(self, monkeypatch):
        log: list[tuple[str, str]] = []
        eng = _Engine("cpu", log)
        import torch.nn.functional as F

        monkeypatch.setattr(
            F, "normalize", lambda t, dim=-1: t.to(eng._device)
        )
        eng.hints_for_text("hypoechoic nodule")
        moved = {d for t, d in log if t.startswith("cache_")}
        assert moved == {"cpu"}

    def test_tensor_module_available(self):
        """防止将来把 torch 依赖从该模块里挪走导致本测试失去意义。"""
        import torch  # noqa: F401
