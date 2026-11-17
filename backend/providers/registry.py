"""Provider 注册表：按 config 选择实现，失败自动回落 mock 并记录告警。"""
from ..config import CFG

_WARNINGS: list[str] = []

_ASR: dict | None = None
_TTS: dict | None = None


def _load(kind: str) -> dict:
    cfg = CFG[kind]
    provider = cfg.get("provider", "mock")
    fallback = cfg.get("fallback", "mock")
    try:
        impl = _instantiate(kind, provider)
    except Exception as e:  # 缺配置/缺依赖 → 回落
        _WARNINGS.append(f"{kind}.provider={provider} 不可用（{e}），回落到 {fallback}")
        impl = _instantiate(kind, fallback)
    return impl


def _instantiate(kind: str, name: str) -> dict:
    if name == "mock":
        from . import mock
        return {"asr": mock.MockASR(), "tts": mock.MockTTS()}[kind]
    if name == "volc":
        from . import volc_asr, volc_tts
        cls = {"asr": volc_asr.VolcASR, "tts": volc_tts.VolcTTS}[kind]
        return cls()
    if name == "deepseek":
        if kind != "asr":
            raise ValueError("DeepSeek 目前只有 ASR 实现")
        from . import deepseek_asr
        return deepseek_asr.DeepSeekASR()
    raise ValueError(f"未知 provider: {name}（{kind}）")


def get_asr() -> dict:
    global _ASR
    if _ASR is None:
        _ASR = _load("asr")
    return _ASR


def get_tts() -> dict:
    global _TTS
    if _TTS is None:
        _TTS = _load("tts")
    return _TTS


def warnings() -> list[str]:
    return list(_WARNINGS)
