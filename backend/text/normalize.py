"""文本规整层（文档 §5.4）：数字/百分比/年份 → TTS 可靠的中文读法。"""
import re

_DIGITS = "零一二三四五六七八九"
_UNITS = ["", "十", "百", "千"]
_GROUP_UNITS = ["", "万", "亿", "万亿"]


def int_to_cn(n: int) -> str:
    """整数 → 中文读法（支持到万亿级）。"""
    if n == 0:
        return "零"
    if n < 0:
        return "负" + int_to_cn(-n)

    def group_to_cn(g: int) -> str:  # 0~9999
        s, zero_pending = "", False
        for pos in (2, 1, 0):  # 百十个
            d = (g // 10 ** pos) % 10
            if d == 0:
                zero_pending = s != ""
            else:
                if zero_pending:
                    s += "零"
                    zero_pending = False
                if d == 1 and pos == 1 and s == "":  # 一十 → 十
                    s += _UNITS[pos]
                else:
                    s += _DIGITS[d] + _UNITS[pos]
        return s

    groups: list[int] = []
    while n:
        groups.append(n % 10000)
        n //= 10000
    parts = []
    for gi in range(len(groups) - 1, -1, -1):
        g = groups[gi]
        if g == 0:
            continue
        parts.append(group_to_cn(g) + _GROUP_UNITS[gi])
        if gi > 0 and groups[gi - 1] and groups[gi - 1] < 1000:
            parts.append("零")
    return re.sub("零+", "零", "".join(parts)).strip("零") or "零"


def _num_to_cn(m: re.Match) -> str:
    s = m.group(0)
    if "." in s:
        ip, dp = s.split(".", 1)
        return int_to_cn(int(ip)) + "点" + "".join(_DIGITS[int(c)] for c in dp)
    return int_to_cn(int(s))


def _year_to_cn(m: re.Match) -> str:
    return "".join(_DIGITS[int(c)] for c in m.group(1)) + "年"


def normalize_text(text: str) -> str:
    """确定性规整，顺序：百分号 → 年份 → 一般数字。"""
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%", lambda m: "百分之" + _num_to_cn(re.match(r"[\d.]+", m.group(1))), text)
    text = re.sub(r"(?<![\d.])([12]\d{3})(?=年)", _year_to_cn, text)
    text = re.sub(r"\d+(?:\.\d+)?", _num_to_cn, text)
    return text


def estimate_duration_zh(text: str, chars_per_sec: float = 4.5) -> float:
    """粗估合成时长，用于改字后即时反馈（中文按字计，数字串按读法后的字数）。"""
    return max(len(text) / chars_per_sec, 0.15)
