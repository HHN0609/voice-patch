"""火山引擎·TTS（声音复刻 + 语音合成）客户端。

⚠️ 声音复刻（音色上传/训练）接口按账号开通版本不同差异较大，上传/提交的
路径与表单字段请以《声音复刻 API 文档》为准。合成接口 (/api/v1/tts) 相对稳定。
"""
import base64
import time
import uuid
import wave
from pathlib import Path

import httpx

from ..config import CFG
from .base import SynthResult

HOST = "https://openspeech.bytedance.com"


class VolcTTS:
    name = "volc"

    def __init__(self) -> None:
        v = CFG["volc"]
        self.appid = v.get("appid") or ""
        self.token = v.get("access_token") or ""
        self.cluster = v.get("tts_cluster") or "volcano_tts"
        self.configured_voice = v.get("voice_type") or ""
        if not (self.appid and self.token):
            raise RuntimeError("火山 TTS 未配置：请填 config.yaml [volc] 或设置 "
                               "VOLC_APP_ID / VOLC_ACCESS_TOKEN")

    # -- 音色复刻 ------------------------------------------------------------
    def clone(self, ref_audio: str, ref_text: str | None = None) -> str:
        """若 config 已填 voice_type 直接复用；否则走复刻上传。

        真实复刻流程（上传 → commit → 训练 → 查询音色 ID）字段随版本变化，
        这里保留调用位，参数以官网文档为准：
          POST /api/v1/mega_tts/audio/upload    （headers: X-Api-App-Key / X-Api-Access-Key / X-Api-Resource-Id）
          POST /api/v1/mega_tts/audio/commit
        """
        if self.configured_voice:
            return self.configured_voice
        raise NotImplementedError(
            "自动复刻接口字段需按你开通的服务版本填写。最快路径：在火山控制台"
            "完成一次音色复刻，把得到的 S_xxxxx 填入 config.yaml volc.voice_type。"
            f"（参考音频已就绪：{ref_audio}）")

    # -- 语音合成 ------------------------------------------------------------
    def synthesize(self, text: str, voice_id: str,
                   speed: float = 1.0, seed: int | None = None) -> SynthResult:
        payload = {
            "app": {"appid": self.appid, "token": self.token, "cluster": self.cluster},
            "user": {"uid": "voice-patch"},
            "audio": {"voice_type": voice_id, "encoding": "wav",
                      "speed_ratio": round(min(max(speed, 0.2), 3.0), 3),
                      "sample_rate": 48000},
            "request": {"reqid": uuid.uuid4().hex, "text": text,
                        "operation": "query", "with_timestamp": "False"},
        }
        r = httpx.post(f"{HOST}/api/v1/tts",
                       headers={"Authorization": f"Bearer; {self.token}",
                                "Content-Type": "application/json"},
                       json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        if data.get("code") not in (3000, "3000", 0, "0", None):
            raise RuntimeError(f"火山 TTS 合成失败: {data.get('message')} (code={data.get('code')})")
        audio_b64 = data.get("data") or ""
        if not audio_b64:
            raise RuntimeError(f"火山 TTS 返回空音频: {data}")
        out_dir = Path(CFG["storage"]["data_dir"])
        out = out_dir / f"volc_tts_{int(time.time() * 1000)}.wav"
        out.write_bytes(base64.b64decode(audio_b64))
        with wave.open(str(out), "rb") as w:
            dur = w.getnframes() / w.getframerate()
        return SynthResult(wav_path=str(out), duration=dur,
                           model_version="volc-tts", seed=seed)
