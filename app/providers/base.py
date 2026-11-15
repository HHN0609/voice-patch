"""Provider 协议（文档 §2.4）。云/本地实现只依赖这三个接口。"""
from typing import Protocol, TypedDict


class Word(TypedDict):
    text: str
    start: float
    end: float
    confidence: float


class SynthResult(TypedDict):
    wav_path: str
    duration: float
    model_version: str
    seed: int | None


class ASRProvider(Protocol):
    name: str

    def transcribe(self, wav_path: str, lang: str = "zh",
                   word_timestamps: bool = True) -> list[Word]: ...


class TTSProvider(Protocol):
    name: str

    def clone(self, ref_audio: str, ref_text: str | None = None) -> str:
        """注册/复刻音色，返回 voice_id。"""

    def synthesize(self, text: str, voice_id: str,
                   speed: float = 1.0, seed: int | None = None) -> SynthResult:
        """合成一段语音，返回 wav 路径与实际时长。"""
