"""时长适配链（文档 §5.1）：语速微调 → 多 take 择优 →（可选）局部变速。

D 类（Δ > 1.5 倍）在编排层拦截，进五选一路由；本模块只处理 A/B 类。
"""
from dataclasses import dataclass, asdict


@dataclass
class TakeFit:
    take_no: int
    raw_dur: float
    speed: float
    stretch: float
    final_dur: float
    status: str   # ok | overflow
    delta: float  # final_dur - W

    def to_dict(self) -> dict:
        return asdict(self)


def auto_speed(raw_dur: float, window: float, lo: float = 0.90, hi: float = 1.10) -> float:
    """求使 raw_dur/speed 最接近 window 的语速（speed>1 加速变短）。"""
    if raw_dur <= 0 or window <= 0:
        return 1.0
    return min(max(raw_dur / window, lo), hi)


def fit_take(raw_dur: float, window: float, speed: float, allow_stretch: bool,
             stretch_range: tuple[float, float], tol_abs: float, tol_rel: float) -> TakeFit:
    """对单个 take 求最终适配方案。stretch>1 表示拉伸变长。"""
    dur = raw_dur / speed
    stretch = 1.0
    tol = max(tol_abs, abs(window) * tol_rel)

    if abs(dur - window) > tol and allow_stretch:
        ratio = dur / window  # >1 需拉长
        lo, hi = stretch_range
        if lo <= ratio <= hi:
            stretch = ratio
            dur = window

    return TakeFit(take_no=0, raw_dur=raw_dur, speed=speed, stretch=round(stretch, 4),
                   final_dur=dur, delta=round(dur - window, 4),
                   status="ok" if abs(dur - window) <= tol else "overflow")


def pick_best(fits: list[TakeFit], window: float) -> TakeFit:
    """优先取 ok 且 |delta| 最小者；全 overflow 时取 |delta| 最小者。"""
    if not fits:
        raise ValueError("没有可用的 take")
    oks = [f for f in fits if f.status == "ok"]
    pool = oks or fits
    return min(pool, key=lambda f: (abs(f.delta), abs(f.final_dur - window)))
