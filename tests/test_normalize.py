"""文本规整：数字 / 百分比 / 年份 → 中文读法。"""
import pytest

from backend.text.normalize import estimate_duration_zh, int_to_cn, normalize_text


@pytest.mark.parametrize("n,expected", [
    (0, "零"),
    (1, "一"),
    (9, "九"),
    (10, "十"),
    (11, "十一"),
    (15, "十五"),
    (20, "二十"),
    (99, "九十九"),
    (100, "一百"),
    (105, "一百零五"),
    (110, "一百一十"),
    (1000, "一千"),
    (1001, "一千零一"),
    (1999, "一千九百九十九"),
    (10000, "一万"),
    (10001, "一万零一"),
    (12345, "一万二千三百四十五"),
    (100000000, "一亿"),
    (-5, "负五"),
])
def test_int_to_cn(n, expected):
    assert int_to_cn(n) == expected


@pytest.mark.parametrize("raw,expected", [
    ("1999.99元", "一千九百九十九点九九元"),
    ("35%", "百分之三十五"),
    ("2026年", "二零二六年"),
    ("3.14", "三点一四"),
    ("第3集", "第三集"),
    ("共120人", "共一百二十人"),
    ("没有数字", "没有数字"),
    ("0.5倍", "零点五倍"),
])
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


def test_normalize_text_幂等():
    once = normalize_text("2026年增长35%，约1.5亿")
    assert normalize_text(once) == once


def test_estimate_duration_zh_与字数相关():
    assert estimate_duration_zh("一二三四五") > estimate_duration_zh("一二三")
    assert estimate_duration_zh("") == pytest.approx(0.15)
