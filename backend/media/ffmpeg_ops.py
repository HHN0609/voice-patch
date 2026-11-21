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


def has_video(path: str | Path) -> bool:
    """文件是否含视频流（纯音频上传时用于决定是否补占位画面）。"""
    _, fp = resolve_bins()
    if fp is not None:
        proc = subprocess.run(
            [fp, "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if proc.returncode == 0:
            try:
                streams = json.loads(proc.stdout).get("streams", [])
            except json.JSONDecodeError:
                streams = []
            return any(s.get("codec_type") == "video" for s in streams)
    proc = _run(["-i", str(path), "-f", "null", "-"])
    return bool(re.search(r"Stream #\d+:\d+.*: Video:", proc.stderr))


def wrap_audio_as_video(audio: str, dst: str, duration: float,
                        width: int = 1280, height: int = 720) -> None:
    """给纯音频补一条黑底视频轨，输出标准 mp4，下游渲染统一按视频处理。"""
    prefix = ["-y", "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=25",
              "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-pix_fmt", "yuv420p"]
    suffix = ["-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.4f}", "-shortest", dst]
    try:
        _run([*prefix, "-c:v", "libx264", "-preset", "veryfast", *suffix])
    except RuntimeError:
        # 部分 ffmpeg 构建没有 libx264，退回 mpeg4（同样可 -c:v copy 封装进 mp4）
        _run([*prefix, "-c:v", "mpeg4", "-q:v", "5", *suffix])


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


def speech_span(wav: str, t0: float, t1: float, noise_db: int = -35, min_d: float = 0.15,
                min_speech: float = 0.08) -> tuple[float, float]:
    """[t0, t1] 内实际语音的绝对起止（把窗口与静音段求补集）。

    长度 < min_speech 的"语音段"视为检测噪声（静音边界抖动会产生几毫秒伪段），
    否则会把紧跟其后的长静音误算进语音跨度，合成时 TTS 提前开口。
    无有效语音时返回 (t0, t1)。
    """
    spans: list[tuple[float, float]] = []
    cur = t0
    for s, e in detect_silences(wav, noise_db, min_d):
        s2, e2 = max(s, t0), min(e, t1)
        if e2 <= s2:
            continue  # 与窗口不相交
        if s2 > cur:
            spans.append((cur, s2))
        cur = max(cur, e2)
    if cur < t1:
        spans.append((cur, t1))
    spans = [(s, e) for s, e in spans if e - s >= min_speech]
    if not spans:
        return t0, t1
    return spans[0][0], spans[-1][1]


def trim_edge_silence(src: str, dst: str, noise_db: int = -35, keep: float = 0.05) -> None:
    """掐头去尾静音（两端各保留 keep 秒过渡），输出语音本体。"""
    sil = f"silenceremove=start_periods=1:start_threshold={noise_db}dB:start_silence={keep}"
    _run(["-y", "-i", src, "-af", f"{sil},areverse,{sil},areverse", "-c:a", "pcm_s16le", dst])


def pad_silence(src: str, head: float, tail: float, dst: str) -> None:
    """头尾补静音使总长精确为 duration(src)+head+tail。"""
    total = duration(src) + head + tail
    h_ms = max(0, round(head * 1000))
    _run(["-y", "-i", src, "-af", f"adelay=delays={h_ms}ms:all=1,apad",
          "-t", f"{total:.4f}", "-c:a", "pcm_s16le", dst])


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
