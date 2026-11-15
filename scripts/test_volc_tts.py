"""真实调用火山 seed-audio TTS 的鉴权/连通性测试（读取 .env，不硬编码密钥）。"""
import base64
import math
import struct
import sys
import uuid
import wave
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import CFG  # noqa: E402  加载 .env

ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/create"
TMP = Path(CFG["storage"]["data_dir"]) / "_volc_test"
TMP.mkdir(parents=True, exist_ok=True)


def make_ref_wav(path: Path, seconds: float = 3.0, freq: int = 220) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000)
        frames = bytearray()
        for i in range(int(seconds * 24000)):
            v = 0.3 * math.sin(2 * math.pi * freq * i / 24000)
            frames += struct.pack("<h", int(v * 32767))
        w.writeframes(bytes(frames))


def main() -> None:
    v = CFG["volc"]
    api_key = v.get("api_key") or v.get("access_token") or ""
    if not api_key:
        print("SKIP: .env 里没有 VOLC_API_KEY / VOLC_ACCESS_TOKEN")
        return
    print(f"鉴权方式: X-Api-Key 单头（值来源: "
          f"{'VOLC_API_KEY' if v.get('api_key') else 'VOLC_ACCESS_TOKEN 兜底'}）")

    ref = TMP / "ref.wav"
    make_ref_wav(ref)
    payload = {
        "model": "seed-audio-1.0",
        "text_prompt": f"@音频1 这台手机的官方售价是一万九千九百九十九点九九元。",
        "references": [{"audio_data": base64.b64encode(ref.read_bytes()).decode()}],
        "audio_config": {"format": "wav", "sample_rate": 48000, "speech_rate": 0},
    }
    r = httpx.post(ENDPOINT, headers={
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "X-Api-Request-Id": uuid.uuid4().hex,
    }, json=payload, timeout=300)
    print(f"HTTP {r.status_code} · logid={r.headers.get('X-Tt-Logid')}")
    data = r.json()
    code, msg = data.get("code"), data.get("message")
    print(f"code={code} message={msg}")
    if data.get("audio"):
        out = TMP / "out.wav"
        out.write_bytes(base64.b64decode(data["audio"]))
        import wave as w2
        with w2.open(str(out), "rb") as w:
            print(f"PASS ✅ 合成成功: {out} · {w.getnframes() / w.getframerate():.2f}s · "
                  f"original_duration={data.get('original_duration')}")
    else:
        print("FAIL ❌ 未返回音频（多半是鉴权/权限问题，见上方 message）")


if __name__ == "__main__":
    main()
