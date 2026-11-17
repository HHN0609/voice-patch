"""ffmpeg / ffprobe 封装（纯函数，无业务逻辑）。

二进制解析顺序：PATH -> imageio_ffmpeg（wheel 自带）-> static_ffmpeg（需联网，默认关闭）。
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

_BINS: tuple[str, str | None] | None = None


def resolve_bins() -> tuple[str, str | None]:
    global _BINS
    if _BINS:
        return _BINS
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ff is None:
        try:  # imageio_ffmpeg 的 wheel 自带 ffmpeg 二进制（本地安装，无需联网）
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    if ff is None and os.environ.get("VOICE_PATCH_STATIC_FFMPEG") == "1":
        try:  # static_ffmpeg 首次需从 GitHub 下载，网络不通时会长时间阻塞，仅显式启用
            from static_ffmpeg import run
            paths = run.get_or_fetch_platform_executables_else_raise()
            ff = ff or paths.get("ffmpeg")
            fp = fp or paths.get("ffprobe")
        except Exception:
            pass
    _BINS = (ff, fp)
    return _BINS


def _run(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    ff, _ = resolve_bins()
    proc = subprocess.run([ff, "-hide_banner", "-loglevel", "info", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"ffmpeg failed: {' '.join(args[:4])}...\n{tail}")
    return proc


def _ffprobe_json(path: str) -> dict:
    _, fp = resolve_bins()
    if fp is None:  # 无 ffprobe 时退化为解析 ffmpeg -i
        proc = _run(["-i", str(path), "-f", "null", "-"])
        text = proc.stderr
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", text)
        dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
        ac = re.search(r"Audio:\s*\w+.*?, (\d+) Hz, (\w+)", text)
        return {"duration": dur,
                "sample_rate": int(ac[1]) if ac else 48000,
                "channels": {"mono": 1, "stereo": 2}.get(ac[2] if ac else "", 2)}
    proc = subprocess.run(
        [fp, "-v", "error", "-show_entries", "format=duration",
         "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr[-400:]}")
    data = json.loads(proc.stdout)
    audio = next((s for s in data.get("streams", []) if s["codec_type"] == "audio"), {})
    return {"duration": float(data.get("format", {}).get("duration") or 0.0),
            "sample_rate": int(audio.get("sample_rate") or 48000),
            "channels": int(audio.get("channels") or 2)}


def probe(path: str | Path) -> dict:
    return _ffprobe_json(str(path))


def duration(path: str | Path) -> float:
    return probe(path)["duration"]


def extract_audio(src: str, dst: str, ar: int = 48000) -> None:
    _run(["-y", "-i", src, "-vn", "-ac", "2", "-ar", str(ar), "-c:a", "pcm_s16le", dst])


def cut(src: str, start: float, end: float, dst: str) -> None:
    """精确裁切（wav 上 -ss 放 -i 之后为逐样本精确）。"""
    _run(["-y", "-i", src, "-ss", f"{start:.4f}", "-to", f"{end:.4f}",
          "-c:a", "pcm_s16le", dst])


def to_pcm16_mono(src: str, dst: str, ar: int = 16000) -> None:
    """转成单声道 16 位 PCM WAV（云 ASR 上传用）。"""
    _run(["-y", "-i", src, "-vn", "-ac", "1", "-ar", str(ar), "-c:a", "pcm_s16le", dst])


def detect_silences(wav: str, noise_db: int = -35, min_d: float = 0.15) -> list[tuple[float, float]]:
    """返回 [(start, end)] 静音段列表。"""
    proc = _run(["-i", wav, "-af", f"silencedetect=noise={noise_db}dB:d={min_d}", "-f", "null", "-"])
    starts = [float(m) for m in re.findall(r"silence_start:\s*(-?[\d.]+)", proc.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", proc.stderr)]
    return list(zip(starts, ends[: len(starts)]))


def atempo(src: str, tempo: float, dst: str) -> None:
    """变速（不保音高）。tempo>1 加速变短。范围 0.5~2.0。"""
    tempo = min(max(tempo, 0.5), 2.0)
    _run(["-y", "-i", src, "-af", f"atempo={tempo:.5f}", "-c:a", "pcm_s16le", dst])


def join_crossfade(parts: list[str], fade_s: float, dst: str) -> None:
    """多段等功率交叉淡化拼接。"""
    assert len(parts) >= 2
    cur = parts[0]
    for i, nxt in enumerate(parts[1:]):
        out = dst if i == len(parts) - 2 else f"{dst}.part{i}.wav"
        _run(["-y", "-i", cur, "-i", nxt, "-filter_complex",
              f"acrossfade=d={fade_s}:c1=tri:c2=tri", "-c:a", "pcm_s16le", out])
        cur = out


def mix_noise_bed(main: str, bed: str, gain: float, dst: str) -> None:
    _run(["-y", "-i", main, "-i", bed, "-filter_complex",
          f"[1:a]volume={gain}[b];[0:a][b]amix=inputs=2:duration=first:dropout_transition=0",
          "-c:a", "pcm_s16le", dst])


def loudnorm_measure(wav: str, i: int = -16, tp: float = -1.5) -> dict:
    proc = _run(["-i", wav, "-af",
                 f"loudnorm=I={i}:TP={tp}:LRA=11:print_format=json", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    if not m:
        return {}
    return json.loads(m[0])


def loudnorm_apply(wav: str, measured: dict, dst: str, i: int = -16, tp: float = -1.5) -> None:
    args = [f"loudnorm=I={i}:TP={tp}:LRA=11"]
    if measured:
        keys = {"measured_I": "input_i", "measured_TP": "input_tp",
                "measured_LRA": "input_lra", "measured_thresh": "input_thresh"}
        extra = [f"{k}={measured[v]}" for k, v in keys.items() if measured.get(v) is not None]
        args = [args[0] + ":" + ":".join(extra)]
    _run(["-y", "-i", wav, "-af", args[0], "-ar", "48000", "-c:a", "pcm_s16le", dst])


def mux_replace_audio(video: str, audio: str, dst: str) -> None:
    """视频流不重编码，仅替换音轨。"""
    _run(["-y", "-i", video, "-i", audio, "-map", "0:v:0", "-map", "1:a:0",
          "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
          "-metadata", "comment=AI-voice-patched", "-shortest", dst])


def make_test_media(dst_video: str, seconds: float = 6.0) -> None:
    """生成测试视频（彩条 + 正弦音），供冒烟测试。"""
    _run(["-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=15",
          "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", dst_video])


def rms_profile(wav: str, win_s: float = 0.5) -> list[tuple[float, float]]:
    """用 wave 模块计算每 win_s 的 RMS 能量，用于参考音频挑选。要求 pcm_s16le。"""
    import array
    import math
    import wave
    with wave.open(wav, "rb") as w:
        nch, sw, fr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        if sw != 2:
            raise ValueError("rms_profile 仅支持 pcm_s16le")
        frames_per_win = int(fr * win_s)
        out: list[tuple[float, float]] = []
        while True:
            raw = w.readframes(frames_per_win)
            if not raw:
                break
            samples = array.array("h")
            samples.frombytes(raw[: len(raw) // 2 * 2])
            if samples:
                rms = math.sqrt(sum(s * s for s in samples) / len(samples))
                out.append((w.tell() / fr, rms))
    return out
