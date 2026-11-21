"""真实闭环测试：TTS 生成语音 → 火山标准版 ASR 转写（pan.ezv.cc 中转）。

验证：鉴权、上传、审核、submit/query 轮询、字级时间戳全链路。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import CFG  # noqa: E402

TMP = Path(CFG["storage"]["data_dir"]) / "_volc_test"


def main() -> None:
    if not CFG["volc"].get("api_key"):
        print("SKIP: .env 里没有密钥")
        return
    from backend.providers.volc_asr import VolcASR
    from backend.providers.volc_tts import VolcTTS

    TMP.mkdir(parents=True, exist_ok=True)
    ref = TMP / "ref.wav"
    speech = TMP / "asr_speech.wav"
    text_expect = "这个手机只卖一千九百九十九点九九元。"

    print("[1/2] TTS 生成测试语音…")
    voice = VolcTTS().clone(str(ref))
    res = VolcTTS().synthesize(text_expect, voice, speed=1.0)
    speech = Path(res["wav_path"])
    print(f"      已生成 {speech.name}（{speech.stat().st_size} bytes）")

    print("[2/2] ASR 转写（pan 中转 → submit → query）…")
    words = VolcASR().transcribe(str(speech))
    text = "".join(w["text"] for w in words)
    print(f"      识别文本: {text!r}")
    print(f"      字级时间戳: {len(words)} 个 · 前3: {words[:3]}")

    assert text.strip(), "识别文本为空"
    assert len(words) >= 3, "字级时间戳缺失"
    print("PASS ✅ 真实 ASR 全链路 OK")


if __name__ == "__main__":
    main()
