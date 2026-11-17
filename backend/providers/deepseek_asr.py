"""DeepSeek 语音识别客户端（同步单次上传，整段文本）。

文档：https://api-docs.deepseek.com/zh-cn/guides/asr
接口：POST /v1/tools/asr，multipart/form-data 的 file 字段；
      音频须为 16 位 PCM WAV 或 Ogg Opus，单次 ≤120s、≤8MiB，双声道只识别左声道。

注意：该接口只返回整段文本与 `duration_ms`，**没有字级时间戳**。
本实现按字符数把总时长均分，生成近似的词级时间——前端“点字跳转”是估算位置。
"""
import re
import tempfile
from pathlib import Path

import httpx

from ..config import CFG
from ..media import ffmpeg_ops as ff
from .base import Word

ENDPOINT = "https://api.deepseek.com/v1/tools/asr"
MAX_SECONDS = 120.0
MAX_BYTES = 8 * 1024 * 1024

# 中文按字切，英文/数字按词切，标点单独成词
_TOKEN = re.compile(r"[A-Za-z0-9']+|[^\sA-Za-z0-9']")


class DeepSeekASR:
    name = "deepseek"

    def __init__(self) -> None:
        ds = CFG.get("deepseek") or {}
        key = ds.get("api_key") or ""
        if not key:
            raise RuntimeError("DeepSeek ASR 未配置：请在 .env 填 DEEPSEEK_API_KEY")
        self.api_key = key
        self.endpoint = ds.get("base_url") or ENDPOINT
        self.hint = "DeepSeek ASR 不返回字级时间戳，字位置为按时长均分的估算值"

    def transcribe(self, wav_path: str, lang: str = "zh",
                   word_timestamps: bool = True) -> list[Word]:
        with tempfile.TemporaryDirectory() as td:
            up = Path(td) / "asr_16k_mono.wav"
            ff.to_pcm16_mono(wav_path, str(up))
            size = up.stat().st_size
            if size > MAX_BYTES:
                raise RuntimeError(f"选区音频 {size / 1048576:.1f}MiB 超过 DeepSeek 8MiB 上限，请缩小选区")
            local_dur = ff.duration(up)
            if local_dur > MAX_SECONDS:
                raise RuntimeError(f"选区 {local_dur:.1f}s 超过 DeepSeek ASR 120s 上限，请缩小选区")
            files = {"file": (up.name, up.read_bytes(), "audio/wav")}

        r = httpx.post(self.endpoint,
                       headers={"Authorization": f"Bearer {self.api_key}"},
                       files=files, timeout=300)
        if r.status_code != 200:
            raise RuntimeError(f"DeepSeek ASR 失败: HTTP {r.status_code} {r.text[:300]}")
        body = r.json()
        dur = float(body.get("duration_ms") or 0) / 1000.0 or local_dur
        return _spread_words((body.get("text") or "").strip(), dur)


def _spread_words(text: str, duration: float) -> list[Word]:
    """把整段文本按时长均分（无字级时间戳时的近似）。"""
    tokens = _TOKEN.findall(text)
    if not tokens or duration <= 0:
        return []
    weights = [len(t) if t.isalnum() else 0.4 for t in tokens]
    total = sum(weights)
    words: list[Word] = []
    t0 = 0.0
    for tok, w in zip(tokens, weights):
        span = duration * w / total
        words.append(Word(text=tok, start=round(t0, 3),
                          end=round(t0 + span, 3), confidence=0.0))
        t0 += span
    return words
