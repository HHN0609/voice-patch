"""LLM 编排顾问的健壮性：解析约束、异常回退、缓存。"""
import json

import httpx
import pytest

from backend.llm import decider

SOURCE_TEXT = "国产新型舰载机"


class FakeResp:
    def __init__(self, content: str, status: int = 200):
        self.status_code = status
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def parse(content: str) -> decider.Advice | None:
    return decider._parse(FakeResp(content), SOURCE_TEXT, 0.95, 1.10)


@pytest.fixture(autouse=True)
def _clear_cache():
    decider._CACHE.clear()
    yield
    decider._CACHE.clear()


class TestParse:
    def test_正常建议可解析(self):
        a = parse('{"speed": 1.05, "pad_side": "head", "text_simplified": null, "reason": "正式播报"}')
        assert a is not None
        assert a.speed == 1.05 and a.pad_side == "head" and a.reason == "正式播报"
        assert a.source == "llm"

    def test_越界语速被夹紧并记录原始值(self):
        a = parse('{"speed": 5.0}')
        assert a.speed == 1.10
        assert a.speed_raw == 5.0
        assert a.speed_clamped is True

    def test_低于下限被夹紧(self):
        a = parse('{"speed": 0.1}')
        assert a.speed == 0.95 and a.speed_clamped is True

    def test_边界内的值不算夹紧(self):
        a = parse('{"speed": 1.05}')
        assert a.speed == 1.05 and a.speed_clamped is False

    def test_非法语速类型被丢弃(self):
        assert parse('{"speed": "fast", "pad_side": "tail"}') is None

    def test_布尔值不被当作数字(self):
        assert parse('{"speed": true}') is None

    def test_坏json返回None(self):
        assert parse("这不是 JSON") is None

    def test_结构不符返回None(self):
        assert decider._parse(FakeResp("{}"), SOURCE_TEXT, 0.95, 1.10) is None

    def test_非法pad_side回退尾部(self):
        a = parse('{"speed": 1.02, "pad_side": "middle"}')
        assert a.pad_side == "tail"

    def test_非法的精简文本被丢弃(self):
        long_text = "明显更长的文本" * 20
        assert parse(json.dumps({"text_simplified": long_text}, ensure_ascii=False)) is None

    def test_空白精简文本被丢弃(self):
        assert parse('{"text_simplified": "   "}') is None

    def test_合理精简被保留(self):
        a = parse('{"text_simplified": "国产舰载机"}')
        assert a.text_simplified == "国产舰载机"

    def test_精简文本去掉换行(self):
        a = parse('{"text_simplified": "国产\\n舰载机"}')
        assert a.text_simplified == "国产 舰载机"

    def test_理由过长被截断(self):
        a = parse(json.dumps({"speed": 1.0, "reason": "很长的理由" * 50}, ensure_ascii=False))
        assert a is not None and len(a.reason) <= 120


class TestEnabled:
    def test_有key且显式开启时可用(self, monkeypatch):
        from backend.config import CFG
        monkeypatch.setitem(CFG["llm"], "api_key", "test-key")
        assert decider.enabled(True) is True
        assert decider.enabled(False) is False

    def test_无key一律不可用(self, monkeypatch):
        from backend.config import CFG
        monkeypatch.setitem(CFG["llm"], "api_key", "")
        monkeypatch.setitem(CFG["deepseek"], "api_key", "")
        assert decider.enabled(True) is False

    def test_请求未指定时跟随配置(self, monkeypatch):
        from backend.config import CFG
        monkeypatch.setitem(CFG["llm"], "api_key", "test-key")
        monkeypatch.setitem(CFG["llm"], "enabled", True)
        assert decider.enabled(None) is True
        monkeypatch.setitem(CFG["llm"], "enabled", False)
        assert decider.enabled(None) is False

    def test_status_不泄露密钥(self, monkeypatch):
        from backend.config import CFG
        monkeypatch.setitem(CFG["llm"], "api_key", "secret-key")
        s = decider.status()
        assert s["available"] is True
        assert "secret-key" not in json.dumps(s)


class TestDecide:
    KW = dict(window=2.02, speech_target=1.77, speech_len=1.95,
              head_pad=0.16, tail_pad=0.09, raw_speech=1.95,
              rule_speed=1.10, video_duration=25.91)

    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        from backend.config import CFG
        monkeypatch.setitem(CFG["llm"], "api_key", "test-key")

    def test_网络异常返回None(self, monkeypatch):
        def boom(*a, **k):
            raise httpx.ConnectTimeout("timeout")
        monkeypatch.setattr(httpx, "post", boom)
        assert decider.decide(text="超时用例", **self.KW) is None

    def test_非200返回None(self, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp("{}", status=500))
        assert decider.decide(text="500用例", **self.KW) is None

    def test_正常响应可解析(self, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp(
            '{"speed": 1.08, "pad_side": "split", "reason": "口语段"}'))
        a = decider.decide(text="成功用例", **self.KW)
        assert a is not None and a.speed == 1.08 and a.pad_side == "split"

    def test_相同输入只请求一次(self, monkeypatch):
        calls = {"n": 0}

        def counting(*a, **k):
            calls["n"] += 1
            return FakeResp('{"speed": 1.03}')

        monkeypatch.setattr(httpx, "post", counting)
        for _ in range(3):
            decider.decide(text="缓存用例", **self.KW)
        assert calls["n"] == 1

    def test_无key不发起请求(self, monkeypatch):
        from backend.config import CFG
        monkeypatch.setitem(CFG["llm"], "api_key", "")
        monkeypatch.setitem(CFG["deepseek"], "api_key", "")
        called = {"n": 0}

        def counting(*a, **k):
            called["n"] += 1
            return FakeResp('{"speed": 1.0}')

        monkeypatch.setattr(httpx, "post", counting)
        assert decider.decide(text="无key用例", **self.KW) is None
        assert called["n"] == 0
