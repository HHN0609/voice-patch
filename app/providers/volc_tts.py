"""火山引擎·音频生成（豆包 seed-audio-1.0，HTTP 非流式）客户端。

文档：https://docs.volcengine.com/docs/doubao-voice/audio-generation-http
- 新版控制台：X-Api-Key 单头鉴权（推荐，无需 app_id）
- 旧版控制台：X-Api-App-Id + X-Api-Access-Key 双头鉴权
- 声音克隆方式：参考音频随请求携带（references[].audio_data ≤30s/≤10MB），
  text_prompt 中用 @音频1 引用；也可用 speaker 传音色 ID（复刻音色或 2.0 音色）
- 语速：speech_rate ∈ [-50,100]，rate = (speed-1)*100（-50 即 0.5 倍速）
"""
import base64
import time
import uuid
import wave
from pathlib import Path

import httpx

from ..config import CFG
from .base import SynthResult

ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/create"
MODEL = "seed-audio-1.0"


class VolcTTS:
    name = "volc"

    def __init__(self) -> None:
        v = CFG["volc"]
        self.api_key = v.get("api_key") or ""
        self.appid = v.get("appid") or ""
        self.token = v.get("access_token") or ""
        self.speaker = v.get("voice_type") or ""
        if self.appid and self.token:
            self.auth = ("dual", self.appid, self.token)      # 旧版双头
        elif self.api_key or self.token:
            # 新版单头：仅填了 access_token 时也按 X-Api-Key 兜底（实测可用）
            self.auth = ("single", self.api_key or self.token)
        else:
            raise RuntimeError("火山 TTS 未配置：请在 .env 填 VOLC_API_KEY（新版控制台，"
                               "推荐，无需 app_id）或 VOLC_APP_ID + VOLC_ACCESS_TOKEN（旧版）")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json",
             "X-Api-Request-Id": uuid.uuid4().hex}
        if self.auth[0] == "single":
            h["X-Api-Key"] = self.auth[1]
        else:
            h["X-Api-App-Id"], h["X-Api-Access-Key"] = self.auth[1], self.auth[2]
        return h

    def clone(self, ref_audio: str, ref_text: str | None = None) -> str:
        """seed-audio 无需预注册音色：返回参考音频标记，synthesize 时随请求携带。"""
        return f"ref:{ref_audio}"

    @staticmethod
    def _speech_rate(speed: float) -> int:
        """speed(1.0=原速) → speech_rate[-50,100]，r=(speed-1)*100。"""
        return max(-50, min(100, round((speed - 1.0) * 100)))

    def synthesize(self, text: str, voice_id: str,
                   speed: float = 1.0, seed: int | None = None) -> SynthResult:
        prompt = text
        references: list[dict] = []
        if voice_id and voice_id.startswith("ref:"):
            path = Path(voice_id[4:])
            audio_b64 = base64.b64encode(path.read_bytes()).decode()
            references = [{"audio_data": audio_b64}]
            prompt = f"@音频1 {text}"
        elif self.speaker:
            references = [{"speaker": self.speaker}]
        else:
            raise RuntimeError("没有可用音色来源（既无参考音频也无 voice_type）")

        payload = {
            "model": MODEL,
            "text_prompt": prompt,
            "references": references,
            "audio_config": {
                "format": "wav",
                "sample_rate": 48000,
                "speech_rate": self._speech_rate(speed),
            },
        }
        r = httpx.post(ENDPOINT, headers=self._headers(), json=payload, timeout=300)
        r.raise_for_status()
        data = r.json()
        if data.get("code") not in (0, "0", None):
            raise RuntimeError(f"火山 TTS 合成失败: code={data.get('code')} "
                               f"message={data.get('message')} logid={r.headers.get('X-Tt-Logid')}")
        audio_b64 = data.get("audio") or ""
        if not audio_b64:
            raise RuntimeError(f"火山 TTS 返回空音频: {data}")
        out = Path(CFG["storage"]["data_dir"]) / f"volc_tts_{int(time.time() * 1000)}.wav"
        out.write_bytes(base64.b64decode(audio_b64))
        with wave.open(str(out), "rb") as w:
            dur = w.getnframes() / w.getframerate()
        return SynthResult(wav_path=str(out), duration=dur,
                           model_version=MODEL, seed=seed)
