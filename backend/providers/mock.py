"""Mock providers：无 Key / 无 GPU 时跑通全链路（联调与冒烟测试用）。

MockTTS 生成与文本长度相关的正弦音，时长带随机抖动以模拟 TTS 时长不确定性。
"""
import math
import random
import struct
import time
import wave
from pathlib import Path

from ..config import CFG
from ..text.normalize import estimate_duration_zh
from .base import SynthResult, Word

_SR = 48000


class MockASR:
    name = "mock"

    def __init__(self) -> None:
        self.hint = "mock ASR 未接真实模型，请在前端手动输入文本"

    def transcribe(self, wav_path: str, lang: str = "zh",
                   word_timestamps: bool = True) -> list[Word]:
        return []  # 空结果 + hint，前端展示手动输入框


class MockTTS:
    name = "mock"

    def clone(self, ref_audio: str, ref_text: str | None = None) -> str:
        return "mock_voice"

    def synthesize(self, text: str, voice_id: str,
                   speed: float = 1.0, seed: int | None = None) -> SynthResult:
        rnd = random.Random(seed)
        base = estimate_duration_zh(text)
        dur = base * (1.0 / max(speed, 0.05)) * rnd.uniform(0.97, 1.10)
        path = str(Path(CFG["storage"]["data_dir"])
                   / f"mock_tts_{int(time.time() * 1000)}_{rnd.randint(0, 9999)}.wav")
        n = int(dur * _SR)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(_SR)
            frames = bytearray()
            for i in range(n):
                t = i / _SR
                env = min(1.0, t * 20, max(0.0, (dur - t) * 20))  # 防爆音包络
                v = 0.35 * env * math.sin(2 * math.pi * 330 * t)
                frames += struct.pack("<h", int(v * 32767))
            w.writeframes(bytes(frames))
        return SynthResult(wav_path=path, duration=n / _SR,
                           model_version="mock-1.0", seed=seed)
