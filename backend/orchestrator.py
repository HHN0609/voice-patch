"""编排层：10 步流水线（文档 §3）。每步落库，可重入。"""
import json
import shutil
from pathlib import Path

from .audio import fitting
from .config import CFG
from .db import execute, new_id, now, query_one
from .llm import decider as llm_decider
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


# ------------------------------------------------------- [2][3] 选区定位 + 裁切
def trim_words(words: list[dict], rel_t1: float, eps: float = 0.02) -> list[dict]:
    """舍弃落在选区终点之后的词（ASR 偶因裁切边界多吐字）。

    rel_t1 是选区终点在裁切音频内的相对位置；词的中点超过它即舍。
    deepseek 无真实字级时间戳时 start/end 为均分估算，midpoint 判定同样近似适用。
    """
    return [w for w in words if (w["start"] + w["end"]) / 2.0 <= rel_t1 + eps]


def transcribe_segment(pid: str, t0: float, t1: float) -> dict:
    proj = get_project(pid)
    audio = proj["audio_path"]
    # 替换区间就是用户框选的区间，不做任何外扩：
    # 外扩会把没框到的音频一并替换掉，且适配目标是语音跨度，外扩也换不来时长收益。
    T0 = round(max(min(t0, t1), 0.0), 4)
    T1 = round(min(max(t0, t1), proj["duration"]), 4)
    seg_wav = str(Path(audio).parent / f"seg_{new_id()}_orig.wav")
    ff.cut(audio, T0, T1, seg_wav)

    asr = registry.get_asr()
    words = trim_words(asr.transcribe(seg_wav), T1 - T0)
    text_raw = "".join(w["text"] for w in words)
    text_norm = normalize_text(text_raw)

    sid = new_id()
    execute("INSERT INTO segments VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, pid, t0, t1, T0, T1, text_raw, text_norm, "transcribed", now()))
    s0, s1 = ff.speech_span(audio, T0, T1)
    return {"segment_id": sid, "t0": t0, "t1": t1, "T0": T0, "T1": T1,
            "window": round(T1 - T0, 4),
            "speech": round(s1 - s0, 4),
            "words": words,
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
                       takes: int | None = None,
                       ref_t0: float | None = None,
                       ref_t1: float | None = None,
                       use_llm: bool | None = None,
                       llm_speed_locked: bool = False) -> dict:
    seg = query_one("SELECT * FROM segments WHERE id=?", (sid,))
    if not seg:
        raise KeyError(f"segment {sid} 不存在")
    proj = get_project(seg["project_id"])

    text_norm = normalize_text(text)
    fade = RENDER["crossfade_ms"] / 1000.0
    window = seg["T1"] - seg["T0"] + 2 * fade  # 预留拼接淡化量

    # 适配目标 = 窗口内的实际语音跨度（ADR 对白替换做法）：
    # 静音扩展出来的留白垫不该由语音去填，否则语音被拉长、take 前后出现多余留白。
    s0, s1 = ff.speech_span(proj["audio_path"], seg["T0"], seg["T1"])
    head_pad = max(s0 - seg["T0"], 0.0)
    tail_pad = max(seg["T1"] - s1, 0.0)
    speech_target = max(s1 - s0, 0.2)

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
    lo, hi = RENDER["speed_range"]
    tol_abs, tol_rel = RENDER["tolerance_abs"], RENDER["tolerance_rel"]

    # 第一遍用原速探时长（掐掉 TTS 自带头尾静音）→ 按语音跨度定语速
    probe_res = tts.synthesize(text_norm, voice_id, speed=1.0, seed=0)
    probe_trim = str(Path(proj["audio_path"]).parent / f"seg_{sid}_probe.wav")
    ff.trim_edge_silence(probe_res["wav_path"], probe_trim)
    raw_speech = ff.duration(probe_trim)
    speed_by_rules = speed if speed is not None else fitting.choose_speed(raw_speech, speech_target, lo, hi)
    speed_by_rules = round(min(max(speed_by_rules, lo), hi), 4)
    speed_use = speed_by_rules
    speed_src = "user" if speed is not None else "rules"

    # 可选：交给大模型微调语速 / 决定优先占用哪侧静音 / 给出精简建议。
    # 失败一律回退规则策略，绝不阻断合成。所有取舍都记进编排账本供前端展示。
    pad_side = "tail"
    plan_source = "rules"
    reason = ""
    text_simplified = None
    llm_speed_raw: float | None = None
    llm_speed_clamped = False
    llm_pad_side: str | None = None
    if llm_decider.enabled(use_llm):
        advice = llm_decider.decide(
            text=text_norm, window=window, speech_target=speech_target,
            speech_len=raw_speech / speed_use if speed_use > 0 else raw_speech,
            head_pad=head_pad, tail_pad=tail_pad, raw_speech=raw_speech,
            rule_speed=speed_use, video_duration=proj["duration"],
        )
        if advice is not None:
            plan_source = "llm"
            reason = advice.reason
            text_simplified = advice.text_simplified
            llm_pad_side = advice.pad_side
            pad_side = advice.pad_side
            llm_speed_raw = advice.speed_raw
            llm_speed_clamped = advice.speed_clamped
            if speed is None and advice.speed is not None and not llm_speed_locked:
                speed_use = advice.speed  # 已由 decider clamp 在硬边界内
                speed_src = "llm"
        else:
            reason = "AI 决策不可用，已回退规则策略"

    fit_list: list[fitting.TakeFit] = []
    out: list[dict] = []
    plan_sample: dict | None = None
    seg_dir = Path(proj["audio_path"]).parent
    for i in range(takes):
        if i == 0:
            raw_path, raw_dur, seed = probe_trim, raw_speech, 0
        else:
            seed = 1000 + i
            # TTS 一律按原速生成，语速统一交给 atempo，保证各 take 缩放一致
            res = tts.synthesize(text_norm, voice_id, speed=1.0, seed=seed)
            raw_path = str(seg_dir / f"seg_{sid}_raw{i + 1}.wav")
            ff.trim_edge_silence(res["wav_path"], raw_path)
            raw_dur = ff.duration(raw_path)

        # 语音本体按语速缩放，再与目标差额摊到两端静音（偏长吃静音、偏短多垫，优先尾部）
        speech_len = raw_dur / speed_use if speed_use > 0 else raw_dur
        voiced = str(seg_dir / f"seg_{sid}_voiced{i + 1}.wav")
        if abs(speed_use - 1.0) > 0.001:
            ff.atempo(raw_path, speed_use, voiced)
        else:
            shutil.copy(raw_path, voiced)
        pad = fitting.plan_pads(speech_len, speech_target, head_pad, tail_pad, side=pad_side)
        if plan_sample is None:  # 账本取第一个 take 的实测值
            plan_sample = {
                "speech_body": round(speech_len, 4),
                "head_before": round(head_pad, 4), "head_after": round(pad.head_pad, 4),
                "tail_before": round(tail_pad, 4), "tail_after": round(pad.tail_pad, 4),
            }

        # 总长恒定对齐 window：头 (head_pad+fade) + 语音 + 尾 (tail_pad+fade)
        final = seg_dir / f"seg_{sid}_take{i + 1}.wav"
        ff.pad_silence(voiced, pad.head_pad + fade, pad.tail_pad + fade, str(final))
        final_dur = ff.duration(str(final))
        f = fitting.TakeFit(
            take_no=i + 1, raw_dur=round(raw_dur, 4), speed=speed_use, stretch=1.0,
            final_dur=round(final_dur, 4), status="ok", delta=round(final_dur - window, 4),
        )
        if pad.overflow or abs(f.delta) > max(tol_abs, speech_target * tol_rel):
            f.status = "overflow"
        take_id = new_id()
        execute("INSERT INTO takes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (take_id, sid, i + 1, text_norm, tts.name, voice_id, speed_use,
                 seed, str(final), raw_dur, round(f.final_dur, 4), f.stretch,
                 f.status, f.delta, 0, now()))
        fit_list.append(f)
        out.append({**f.to_dict(), "take_id": take_id,
                    "wav": f"/data/projects/{proj['id']}/{Path(final).name}"})

    best = fitting.pick_best(fit_list)
    overflow = best.status != "ok"
    execute("UPDATE segments SET text_norm=?, status=? WHERE id=?",
            (text_norm, "synthesized", sid))
    sample = plan_sample or {"speech_body": 0.0, "head_before": 0.0, "head_after": 0.0,
                             "tail_before": 0.0, "tail_after": 0.0}
    plan = {
        "source": plan_source,
        "reason": reason,
        "text_simplified": text_simplified,
        "speed": {"rules": speed_by_rules, "llm_raw": llm_speed_raw,
                  "applied": speed_use, "clamped": llm_speed_clamped, "source": speed_src},
        "pad_side": {"rules": "tail", "llm_raw": llm_pad_side, "applied": pad_side,
                     "source": "llm" if llm_pad_side else "rules"},
        "speech": {"target": round(speech_target, 4), "body": sample["speech_body"],
                   "delta": round(sample["speech_body"] - speech_target, 4)},
        "silence": {"head_before": sample["head_before"], "head_after": sample["head_after"],
                    "tail_before": sample["tail_before"], "tail_after": sample["tail_after"]},
    }

    return {"segment_id": sid, "text_norm": text_norm, "speed": speed_use,
            "window": round(window, 4), "speech": round(speech_target, 4), "takes": out,
            "voice_source": voice_source, "plan": plan,
            "overflow": overflow,
            "delta": best.delta,
            "d_class": bool(raw_speech > speech_target * 1.5)}  # 语音远超目标，按 D 类提示五选一路由


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
