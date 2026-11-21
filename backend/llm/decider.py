"""LLM 编排顾问：把语速微调与静音占用方向交给大模型判断。

健壮性约束（harness 硬规则）：
- LLM 只产出“建议”：每个字段都有硬边界，越界一律 clamp，类型不符直接丢弃；
- 无 key / 超时 / 非 200 / 坏 JSON / 结构不符，一律返回 None，主流程走规则引擎，绝不抛错；
- LLM 不能改变流水线结构，也不能决定“接受溢出”（该风险由规则层把关）；
- 精简文本只作为建议返回给用户确认，不自动替换；
- 相同输入在进程内缓存，避免重复计费。
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass

import httpx

from ..config import CFG

PAD_SIDES = ("tail", "head", "split")
_MAX_CACHE = 32

_CACHE: dict[str, "Advice | None"] = {}
_LOCK = threading.Lock()


@dataclass
class Advice:
    speed: float | None = None        # 已夹紧到硬边界的最终建议；None = 不覆盖规则语速
    speed_raw: float | None = None    # AI 的原始建议（夹紧前），用于向用户明示是否被收敛
    speed_clamped: bool = False
    pad_side: str = "tail"
    text_simplified: str | None = None
    reason: str = ""
    source: str = "llm"


def api_key() -> str:
    llm = CFG.get("llm") or {}
    return llm.get("api_key") or (CFG.get("deepseek") or {}).get("api_key") or ""


def enabled(requested: bool | None = None) -> bool:
    """请求里显式给了开关就用它（临时开启），否则看配置。没 key 一律不可用。"""
    if not api_key():
        return False
    if requested is not None:
        return bool(requested)
    return bool((CFG.get("llm") or {}).get("enabled"))


def status() -> dict:
    llm = CFG.get("llm") or {}
    return {"enabled": bool(llm.get("enabled")), "available": bool(api_key()),
            "model": llm.get("model") or "deepseek-chat"}


SYSTEM_PROMPT = """你是口误修复工具的时长编排顾问。输入是一次 TTS 合成的测量数据，
请在给定硬边界内给出编排建议。

硬约束（越界值会被系统夹紧，请勿尝试突破）：
- speed: 0.95 ~ 1.10（1.0 为原速，值越大语速越快、语音越短）
- pad_side: "tail" | "head" | "split"

判断原则：
- speech_target 是用户选区内的实际语音跨度，新语音应尽量贴近它；
- speech_len = raw_speech / speed，是你选定语速后的预测语音长度；
- 语音偏长时：可加快语速，或占用选区自带的静音（pad_side 决定先吃哪侧）；
- 语音偏短时：可放慢语速，或在静音侧多垫静音；
- 正式播报 / 解说 / 严肃场合：语速应贴近 1.0，优先用静音缓冲，避免变速带来的不自然；
- 口语化、节奏快的片段：可以适度加快到 1.05~1.10；
- 句尾收束明显（语气下沉、拖音）时优先 pad_side="head"，否则优先 "tail"；
- 若语音明显超出可容纳长度，给出一个更短的同义说法（text_simplified）：
  只能删冗余词、口语化数字，严禁改变事实、增删信息。

只返回 JSON 对象，不要任何额外文字：
{"speed": 1.0, "pad_side": "tail", "text_simplified": null, "reason": "不超过 40 字的中文理由"}"""


def decide(*, text: str, window: float, speech_target: float, speech_len: float,
           head_pad: float, tail_pad: float, raw_speech: float,
           rule_speed: float, video_duration: float,
           timeout_s: float | None = None) -> Advice | None:
    """返回编排建议；任何异常都返回 None，由调用方走规则引擎。"""
    key = api_key()
    if not key:
        return None

    cache_key = (f"{text}|{speech_target:.3f}|{raw_speech:.3f}"
                 f"|{head_pad:.3f}|{tail_pad:.3f}|{rule_speed:.3f}")
    with _LOCK:
        if cache_key in _CACHE:
            return _CACHE[cache_key]

    cfg = CFG.get("llm") or {}
    lo, hi = _speed_bounds()
    payload = {
        "model": cfg.get("model") or "deepseek-chat",
        "temperature": 0.2,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({
                "text": text,
                "video_duration_s": round(video_duration, 3),
                "window_s": round(window, 3),
                "speech_target_s": round(speech_target, 3),
                "raw_speech_s": round(raw_speech, 3),
                "predicted_speech_s": round(speech_len, 3),
                "head_silence_s": round(head_pad, 3),
                "tail_silence_s": round(tail_pad, 3),
                "rule_speed": round(rule_speed, 3),
                "speed_bounds": [lo, hi],
            }, ensure_ascii=False)},
        ],
    }

    base = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    try:
        r = httpx.post(f"{base}/chat/completions",
                       headers={"Authorization": f"Bearer {key}"},
                       json=payload,
                       timeout=timeout_s or float(cfg.get("timeout_s") or 8))
    except Exception:
        advice = None
    else:
        advice = _parse(r, text, lo, hi) if r.status_code == 200 else None

    with _LOCK:
        if len(_CACHE) >= _MAX_CACHE:
            _CACHE.clear()
        _CACHE[cache_key] = advice
    return advice


def _speed_bounds() -> tuple[float, float]:
    llm = CFG.get("llm") or {}
    bounds = llm.get("speed_clamp")
    if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
        return float(bounds[0]), float(bounds[1])
    sr = (CFG.get("render") or {}).get("speed_range") or [0.95, 1.10]
    return float(sr[0]), float(sr[1])


def _num(v, lo: float, hi: float) -> float | None:
    """只接受有限数值；越界 clamp 到边界，非法类型返回 None。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return round(min(max(f, lo), hi), 4)


def _parse(resp: httpx.Response, source_text: str, lo: float, hi: float) -> Advice | None:
    try:
        content = resp.json()["choices"][0]["message"]["content"]
        obj = json.loads(content)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None

    advice = Advice(source="llm")
    advice.speed = _num(obj.get("speed"), lo, hi)
    if advice.speed is not None:
        advice.speed_raw = float(obj["speed"])
        advice.speed_clamped = abs(advice.speed_raw - advice.speed) > 1e-6

    side = obj.get("pad_side")
    if isinstance(side, str) and side.strip().lower() in PAD_SIDES:
        advice.pad_side = side.strip().lower()

    ts = obj.get("text_simplified")
    if isinstance(ts, str):
        ts = ts.strip().replace("\n", " ")
        # 必须是“精简”：非空、且不显著长于原文，否则视为幻觉丢弃
        if ts and len(ts) <= max(len(source_text) * 1.2, len(source_text) + 4):
            advice.text_simplified = ts

    reason = obj.get("reason")
    if isinstance(reason, str):
        advice.reason = reason.strip()[:120]

    # 一个有效字段都没有 → 视为无效建议
    if advice.speed is None and advice.text_simplified is None and advice.pad_side == "tail":
        return None
    return advice
