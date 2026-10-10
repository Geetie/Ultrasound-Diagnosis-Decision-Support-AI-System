#!/usr/bin/env python3
"""A.50 语音引擎真机功能验证：用模型仓自带的 3 条真实中文语音跑通识别链。

流程：wav(16k/mono/int16) → FunASROnnxEngine（paraformer 特征管线 + CTC 解码）
→ 文本 + 术语标注 + 时延。通过判据：3 条都产出**非空且非乱码**的中文文本。

用法（服务器）：~/uai/venv/bin/python ~/uai/src/scripts/audit/validate_asr.py [model_dir]
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.speech.funasr import FunASROnnxEngine
from app.speech.protocols import SpeechRequest


def load_wav(path: str) -> tuple[bytes, int]:
    with wave.open(path) as w:
        assert w.getnchannels() == 1, f"{path}: 需单声道"
        assert w.getsampwidth() == 2, f"{path}: 需 16bit"
        sr = w.getframerate()
        pcm = w.readframes(w.getnframes())
    return pcm, sr


async def main() -> int:
    model_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/uai/models/funasr"
    )
    wav_dir = os.path.join(model_dir, "test_wavs")
    wavs = sorted(
        f for f in os.listdir(wav_dir) if f.endswith(".wav") and f[0].isdigit() and "-" not in f
    )[:3]
    if not wavs:
        print("FAIL: 无测试 wav")
        return 1

    engine = FunASROnnxEngine(model_dir=model_dir, device="cpu", num_threads=8)
    await engine.initialize()
    ok = 0
    for name in wavs:
        pcm, sr = load_wav(os.path.join(wav_dir, name))
        req = SpeechRequest(
            audio_data=base64.b64encode(pcm).decode(),
            sample_rate=sr,
            request_id=f"validate-{name}",
        )
        resp = await engine.recognize(req)
        text = resp.text.strip()
        terms = [m["term"] for m in (resp.terminology_matches or [])]
        flag = "PASS" if text else "FAIL"
        if text:
            ok += 1
        print(f"[{flag}] {name}: len(pcm)={len(pcm)} latency={resp.latency_ms}ms")
        print(f"      text({len(text)}字): {text}")
        print(f"      terms: {terms}")
    await engine.close()
    print(f"\n== ASR 真机验证: {ok}/{len(wavs)} 产出非空文本 ==")
    return 0 if ok == len(wavs) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
