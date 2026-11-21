"""时长适配：选语速 → 用选区自带的静音吸收差额 → 多 take 择优。

语音长度与目标（选区内的语音跨度）不一致时按以下优先级处理：
- 偏长：先加快语速（≤ speed_hi），仍不够再占用选区内的静音（优先尾部）
- 偏短：先放慢语速（≥ speed_lo），仍不够就多垫静音（优先尾部）

静音缓冲用尽仍塞不下时标记 overflow，由上层提示精简文本或扩大选区。
语音变速统一用 ffmpeg atempo 完成，TTS 侧固定按原速生成，保证各 take 缩放一致。
"""
from dataclasses import asdict, dataclass


@dataclass
class PadPlan:
    head_pad: float   # 头部静音
    tail_pad: float   # 尾部静音
    overflow: bool    # 语速与静音缓冲都用满仍塞不下


@dataclass
class TakeFit:
    take_no: int
    raw_dur: float
    speed: float
    stretch: float
    final_dur: float
    status: str   # ok | overflow
    delta: float  # final_dur - window

    def to_dict(self) -> dict:
        return asdict(self)


def choose_speed(raw_speech: float, speech_target: float,
                 lo: float = 0.95, hi: float = 1.10) -> float:
    """求使 raw_speech/speed 最接近 speech_target 的语速（speed>1 加速变短）。"""
    if raw_speech <= 0 or speech_target <= 0:
        return 1.0
    return round(min(max(raw_speech / speech_target, lo), hi), 4)


def plan_pads(speech_len: float, speech_target: float,
              head_pad: float, tail_pad: float) -> PadPlan:
    """把语音与目标的差额摊到两端静音：偏长吃静音、偏短多垫静音，都优先尾部。"""
    head, tail = head_pad, tail_pad
    diff = speech_len - speech_target
    overflow = False
    if diff > 0:
        take = min(tail, diff)
        tail -= take
        diff -= take
        take = min(head, diff)
        head -= take
        diff -= take
        overflow = diff > 1e-6
    elif diff < 0:
        tail += -diff
    return PadPlan(round(max(head, 0.0), 4), round(max(tail, 0.0), 4), overflow)


def pick_best(fits: list[TakeFit]) -> TakeFit:
    """优先取 ok 且 |delta| 最小者；全 overflow 时取 |delta| 最小者。"""
    if not fits:
        raise ValueError("没有可用的 take")
    oks = [f for f in fits if f.status == "ok"]
    pool = oks or fits
    return min(pool, key=lambda f: abs(f.delta))
