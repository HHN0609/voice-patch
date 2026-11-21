"""全链路集成测试（mock provider，不触网）：转写 → 合成 → 渲染，校验等长替换。"""
import pytest

from backend import orchestrator as orc
from backend.media import ffmpeg_ops as ff


@pytest.fixture()
def project(tmp_data, media_dir, mock_providers):
    src = str(media_dir / "clip.mp4")
    ff.make_test_media(src, 6.0)
    return orc.create_project("clip.mp4", src)


@pytest.fixture()
def media_dir(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    return d


def _render(proj: dict) -> tuple[float, float]:
    """跑一遍完整流水线，返回 (原片时长, 成片时长)。"""
    seg = orc.transcribe_segment(proj["id"], 2.0, 3.2)
    synth = orc.synthesize_segment(seg["segment_id"], "一九九九点九九元")
    best = min(synth["takes"], key=lambda t: abs(t["delta"]))
    orc.render_segment(seg["segment_id"], best["take_id"])
    out = orc.get_project(proj["id"])
    final = out["video_path"].replace("source.mp4", "patch_final.mp4")
    return proj["duration"], ff.duration(final)


def test_转写窗口等于选区(project):
    seg = orc.transcribe_segment(project["id"], 2.0, 3.2)
    assert seg["T0"] == pytest.approx(2.0)
    assert seg["T1"] == pytest.approx(3.2)
    assert seg["window"] == pytest.approx(1.2, abs=1e-6)


def test_合规文本成片等长(project):
    d_orig, d_new = _render(project)
    assert abs(d_new - d_orig) < 0.05


def test_短文本不会缩短成片(tmp_data, media_dir, mock_providers):
    """回归：语音远短于选区时，必须垫静音补齐，而不是让成片变短。"""
    src = str(media_dir / "short.mp4")
    ff.make_test_media(src, 8.0)
    proj = orc.create_project("short.mp4", src)

    seg = orc.transcribe_segment(proj["id"], 2.0, 5.0)   # 3s 选区
    synth = orc.synthesize_segment(seg["segment_id"], "你好")  # 极短文本
    best = min(synth["takes"], key=lambda t: abs(t["delta"]))
    orc.render_segment(seg["segment_id"], best["take_id"])

    final = orc.get_project(proj["id"])["video_path"].replace("source.mp4", "patch_final.mp4")
    d_new = ff.duration(final)
    assert abs(d_new - proj["duration"]) < 0.05, f"成片被缩短：{d_new} vs {proj['duration']}"

    # take 总长应贴合替换窗口（不算拼接预留的 crossfade）
    assert abs(best["final_dur"] - synth["window"]) < 0.06


def test_take长度恒等窗口(project):
    seg = orc.transcribe_segment(project["id"], 2.0, 3.2)
    synth = orc.synthesize_segment(seg["segment_id"], "一九九九点九九元")
    for take in synth["takes"]:
        # 允许语速/静音缓冲都用尽时的溢出，正常情况应贴合窗口
        if take["status"] == "ok":
            assert abs(take["final_dur"] - synth["window"]) < 0.06


def test_编排账本字段完整(project):
    seg = orc.transcribe_segment(project["id"], 2.0, 3.2)
    plan = orc.synthesize_segment(seg["segment_id"], "一九九九点九九元")["plan"]

    assert plan["source"] == "rules"
    assert plan["speed"]["source"] == "rules"
    assert plan["speed"]["llm_raw"] is None
    assert plan["pad_side"]["applied"] in ("tail", "head", "split")
    assert plan["speech"]["target"] > 0
    for key in ("head_before", "head_after", "tail_before", "tail_after"):
        assert key in plan["silence"]
