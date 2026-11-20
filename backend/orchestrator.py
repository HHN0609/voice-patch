"""编排层：10 步流水线（文档 §3）。每步落库，可重入。"""
import json
import shutil
from pathlib import Path

from .audio import fitting
from .config import CFG
from .db import execute, new_id, now, query_one
from .media import ffmpeg_ops as ff
from .providers import registry
from .text.normalize import normalize_text

RENDER = CFG["render"]


# ---------------------------------------------------------------- [1] 上传/抽轨
def create_project(name: str, video_path: str) -> dict:
    pid = new_id()
    pdir = Path(CFG["storage"]["data_dir"]) / "projects" / pid
    pdir.mkdir(parents=True, exist_ok=True)
    ext = Path(video_path).suffix or ".mp4"
    dst_video = pdir / f"source{ext}"
    shutil.move(video_path, dst_video)
    info = ff.probe(str(dst_video))
    audio = pdir / "audio_48k.wav"
    ff.extract_audio(str(dst_video), str(audio))
    execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?)",
            (pid, name, str(dst_video), str(audio), info["duration"], None, now()))
    return {"id": pid, "name": name, "duration": info["duration"],
            "video_url": f"/data/projects/{pid}/{dst_video.name}",
            "audio_url": f"/data/projects/{pid}/{audio.name}"}


def get_project(pid: str) -> dict:
    p = query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        raise KeyError(f"project {pid} 不存在")
    p["video_url"] = f"/data/projects/{pid}/{Path(p['video_path']).name}"
    p["audio_url"] = f"/data/projects/{pid}/{Path(p['audio_path']).name}"
    return p


# ------------------------------------------------------- [2][3] 选区扩展 + 裁切
def expand_region(t0: float, t1: float, silences: list[tuple[float, float]],
                  audio_dur: float, max_expand: float) -> tuple[float, float]:
    """把 [t0,t1] 扩到最近的静音边界（文档 §3 [3]），扩展量受限。"""
    before = [e for s, e in silences if e <= t0 + 1e-6]
    after = [s for s, _ in silences if s >= t1 - 1e-6]
    T0 = min(max(t0 - max_expand, max(before) if before else t0 - 0.25), t0)
    T1 = max(min(t1 + max_expand, min(after) if after else t1 + 0.25), t1)
    return round(max(T0, 0.0), 4), round(min(T1, audio_dur), 4)


def transcribe_segment(pid: str, t0: float, t1: float) -> dict:
    proj = get_project(pid)
    audio = proj["audio_path"]
    silences = ff.detect_silences(audio)
    T0, T1 = expand_region(t0, t1, silences, proj["duration"],
                           RENDER["region_expand_max"])
    seg_wav = str(Path(audio).parent / f"seg_{new_id()}_orig.wav")
    ff.cut(audio, T0, T1, seg_wav)

    asr = registry.get_asr()
    words = asr.transcribe(seg_wav)
    text_raw = "".join(w["text"] for w in words)
    text_norm = normalize_text(text_raw)

    sid = new_id()
    execute("INSERT INTO segments VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, pid, t0, t1, T0, T1, text_raw, text_norm, "transcribed", now()))
    return {"segment_id": sid, "t0": t0, "t1": t1, "T0": T0, "T1": T1,
            "window": round(T1 - T0, 4), "words": words,
            "text_raw": text_raw, "text_norm": text_norm,
            "asr_hint": getattr(asr, "hint", None)}


# ------------------------------------------------ [6][7][8] 时长适配/参考音/TTS
_REF_MIN_LEN = 2.5  # 参考音最短时长（秒），过短时向两侧补足


def cut_ref(proj: dict, start: float, end: float) -> str:
    """从原音轨裁一段参考音，返回文件路径。"""
    ref = str(Path(proj["audio_path"]).parent / f"ref_{new_id()}.wav")
    ff.cut(proj["audio_path"], start, end, ref)
    return ref


def ref_window(T0: float, T1: float, duration: float) -> tuple[float, float]:
    """未框选参考音时的参考窗口：口误区间本身；不足 _REF_MIN_LEN 时向两侧补足。"""
    if T1 - T0 >= _REF_MIN_LEN:
        return T0, T1
    center = (T0 + T1) / 2
    start = max(0.0, min(center - _REF_MIN_LEN / 2, duration - _REF_MIN_LEN))
    return round(start, 4), round(min(duration, start + _REF_MIN_LEN), 4)


def synthesize_segment(sid: str, text: str, speed: float | None = None,
                       allow_stretch: bool = False, takes: int | None = None,
                       ref_t0: float | None = None,
                       ref_t1: float | None = None) -> dict:
    seg = query_one("SELECT * FROM segments WHERE id=?", (sid,))
    if not seg:
        raise KeyError(f"segment {sid} 不存在")
    proj = get_project(seg["project_id"])

    text_norm = normalize_text(text)
    window = seg["T1"] - seg["T0"] + 2 * RENDER["crossfade_ms"] / 1000.0  # 预留拼接淡化量

    tts = registry.get_tts()
    # 参考音：优先用户框选的“同说话人”区间（多人视频必需）；
    # 未选时默认用口误区间本身（同一说话人、同一声学环境），过短则向两侧补足。
    if ref_t0 is not None and ref_t1 is not None:
        if ref_t1 - ref_t0 < 1.0:
            raise ValueError("参考音区间太短（<1s），请框选一段完整的话")
        # 只跟用户看得见的红区比较；T0/T1 是静音扩展后的隐藏窗口，不能拿来判重叠
        if not (ref_t1 <= seg["t0_sel"] or ref_t0 >= seg["t1_sel"]):
            raise ValueError("参考音区间不能与口误区间重叠")
        ref = cut_ref(proj, ref_t0, min(ref_t1, proj["duration"]))
        voice_source = "user"
    else:
        r0, r1 = ref_window(seg["T0"], seg["T1"], proj["duration"])
        ref = cut_ref(proj, r0, r1)
        voice_source = "mispeak"

    voice_id = tts.clone(ref)
    execute("UPDATE projects SET voice_id=? WHERE id=?", (voice_id, proj["id"]))

    takes = takes or CFG["tts"].get("takes", 3)
    # 第一遍用 1.0 探时长 → 自动求速；后续 take 都按该速度生成
    probe_res = tts.synthesize(text_norm, voice_id, speed=1.0, seed=0)
    auto_sp = speed if speed else fitting.auto_speed(probe_res["duration"], window,
                                                     *RENDER["speed_range"])

    lo, hi = RENDER["stretch_range"]
    tol_abs, tol_rel = RENDER["tolerance_abs"], RENDER["tolerance_rel"]
    fit_list: list[fitting.TakeFit] = []
    out: list[dict] = []
    seg_dir = Path(proj["audio_path"]).parent
    for i in range(takes):
        if i == 0:
            raw_path, raw_dur, seed = probe_res["wav_path"], probe_res["duration"], 0
        else:
            seed = 1000 + i
            res = tts.synthesize(text_norm, voice_id, speed=auto_sp, seed=seed)
            raw_path, raw_dur = res["wav_path"], res["duration"]
        f = fitting.fit_take(raw_dur, window, auto_sp, allow_stretch, (lo, hi), tol_abs, tol_rel)
        f.take_no = i + 1
        final = seg_dir / f"seg_{sid}_take{i + 1}.wav"
        # 精确等长：tempo = 原始时长 / 目标时长（覆盖语速应用与局部拉伸两种情况）
        tempo = raw_dur / f.final_dur if f.final_dur > 0 else 1.0
        if abs(tempo - 1.0) > 0.001:
            ff.atempo(raw_path, tempo, str(final))
        else:
            shutil.copy(raw_path, final)
        f.final_dur = ff.duration(str(final))  # 以实际输出为准
        f.delta = round(f.final_dur - window, 4)
        f.status = "ok" if abs(f.delta) <= max(tol_abs, window * tol_rel) else f.status
        take_id = new_id()
        execute("INSERT INTO takes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (take_id, sid, i + 1, text_norm, tts.name, voice_id, round(auto_sp, 4),
                 seed, str(final), raw_dur, round(f.final_dur, 4), f.stretch,
                 f.status, f.delta, 0, now()))
        fit_list.append(f)
        out.append({**f.to_dict(), "take_id": take_id,
                    "wav": f"/data/projects/{proj['id']}/{Path(final).name}"})

    best = fitting.pick_best(fit_list, window)
    overflow = best.status != "ok"
    execute("UPDATE segments SET text_norm=?, status=? WHERE id=?",
            (text_norm, "synthesized", sid))
    return {"segment_id": sid, "text_norm": text_norm, "speed": round(auto_sp, 4),
            "window": round(window, 4), "takes": out,
            "voice_source": voice_source,
            "overflow": overflow,
            "delta": best.delta,
            "d_class": bool(best.delta > window * 0.5)}  # Δ 超过窗口一半，按 D 类提示五选一路由


# --------------------------------------------- [9][10] 拼接/底噪/响度/导出
def _pick_noise_bed(audio: str, T0: float, T1: float) -> str | None:
    for s, e in ff.detect_silences(audio):
        mid = (s + e) / 2
        if T0 <= mid <= T1:
            continue
        b0, b1 = s, min(s + 0.4, e)
        if b1 - b0 >= 0.2:
            bed = str(Path(audio).parent / f"bed_{new_id()}.wav")
            ff.cut(audio, b0, b1, bed)
            return bed
    return None


def render_segment(sid: str, take_id: str) -> dict:
    seg = query_one("SELECT * FROM segments WHERE id=?", (sid,))
    take = query_one("SELECT * FROM takes WHERE id=?", (take_id,))
    if not seg or not take or take["segment_id"] != sid:
        raise KeyError("segment/take 不匹配")
    proj = get_project(seg["project_id"])
    audio = proj["audio_path"]
    fade = RENDER["crossfade_ms"] / 1000.0
    seg_dir = Path(audio).parent

    head = str(seg_dir / f"head_{sid}.wav")
    tail = str(seg_dir / f"tail_{sid}.wav")
    ff.cut(audio, 0.0, seg["T0"], head)
    ff.cut(audio, seg["T1"], proj["duration"], tail)

    joined = str(seg_dir / f"joined_{sid}.wav")
    parts = [p for p in [head, take["wav_path"], tail]
             if ff.duration(p) > fade * 2]
    ff.join_crossfade(parts, fade, joined)

    bed = _pick_noise_bed(audio, seg["T0"], seg["T1"])
    if bed:
        withbed = str(seg_dir / f"withbed_{sid}.wav")
        ff.mix_noise_bed(joined, bed, RENDER["noise_bed_gain"], withbed)
        joined = withbed

    measured = ff.loudnorm_measure(joined, RENDER["target_lufs"], RENDER["true_peak"])
    final_audio = str(seg_dir / f"final_{sid}.wav")
    ff.loudnorm_apply(joined, measured, final_audio, RENDER["target_lufs"], RENDER["true_peak"])

    out_video = seg_dir / "patch_final.mp4"
    ff.mux_replace_audio(proj["video_path"], final_audio, str(out_video))
    execute("UPDATE takes SET selected=1 WHERE id=?", (take_id,))
    execute("UPDATE takes SET selected=0 WHERE id=? AND id!=? AND segment_id=?",
            (take_id, take_id, sid))
    execute("UPDATE segments SET status='rendered' WHERE id=?", (sid,))
    return {"video": f"/data/projects/{proj['id']}/{out_video.name}",
            "original_audio": f"/data/projects/{proj['id']}/audio_48k.wav",
            "final_dur": take["final_dur"], "delta": take["delta"]}
