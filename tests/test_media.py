"""ffmpeg 封装：静音检测、语音跨度、补静音、纯音频补画面。"""
import pytest

from backend.media import ffmpeg_ops as ff


@pytest.fixture(scope="module")
def tone_with_silence(tmp_path_factory):
    """0.5s 静音 + 1s 正弦 + 0.5s 静音，用于验证静音相关原语。"""
    d = tmp_path_factory.mktemp("media")
    head, mid, tail = d / "h.wav", d / "m.wav", d / "t.wav"
    ff._run(["-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "0.5", str(head)])
    ff._run(["-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
             "-ac", "2", "-ar", "48000", str(mid)])
    ff._run(["-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "0.5", str(tail)])
    out = d / "tone.wav"
    ff.join_crossfade([str(head), str(mid), str(tail)], 0.02, str(out))
    return out


class TestHasVideo:
    def test_视频文件应检出视频流(self, tmp_path):
        v = str(tmp_path / "clip.mp4")
        ff.make_test_media(v, 1.5)
        assert ff.has_video(v) is True

    def test_纯音频文件应判为无视频(self, tmp_path):
        a = str(tmp_path / "voice.mp3")
        ff._run(["-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=2",
                 "-ac", "1", "-ar", "44100", a])
        assert ff.has_video(a) is False


class TestSpeechSpan:
    def test_整段窗口返回语音起止(self, tone_with_silence):
        dur = ff.duration(tone_with_silence)
        s0, s1 = ff.speech_span(str(tone_with_silence), 0.0, dur)
        assert s0 == pytest.approx(0.5, abs=0.08)
        assert s1 == pytest.approx(1.5, abs=0.08)

    def test_窗口全在语音内时原样返回(self, tone_with_silence):
        s0, s1 = ff.speech_span(str(tone_with_silence), 0.7, 1.3)
        assert s0 == pytest.approx(0.7, abs=0.02)
        assert s1 == pytest.approx(1.3, abs=0.02)

    def test_过滤边界噪声产生的伪语音段(self, tone_with_silence):
        """窗口起点落在静音里时，不应把紧跟的长静音算进语音跨度。"""
        dur = ff.duration(tone_with_silence)
        s0, _ = ff.speech_span(str(tone_with_silence), 0.0, dur)
        assert s0 >= 0.4  # 而不是 0.0


class TestSilenceOps:
    def test_掐头去尾只剩语音本体(self, tone_with_silence, tmp_path):
        out = str(tmp_path / "trimmed.wav")
        ff.trim_edge_silence(str(tone_with_silence), out)
        assert ff.duration(out) == pytest.approx(1.0, abs=0.15)

    def test_补静音后总长精确(self, tone_with_silence, tmp_path):
        trimmed = str(tmp_path / "trimmed.wav")
        ff.trim_edge_silence(str(tone_with_silence), trimmed)
        base = ff.duration(trimmed)
        out = str(tmp_path / "padded.wav")
        ff.pad_silence(trimmed, 0.3, 0.7, out)
        assert ff.duration(out) == pytest.approx(base + 1.0, abs=0.05)

    def test_补零静音不改变长度(self, tone_with_silence, tmp_path):
        out = str(tmp_path / "same.wav")
        ff.pad_silence(str(tone_with_silence), 0.0, 0.0, out)
        assert ff.duration(out) == pytest.approx(ff.duration(tone_with_silence), abs=0.05)


class TestAtempo:
    def test_加速后变短(self, tone_with_silence, tmp_path):
        out = str(tmp_path / "fast.wav")
        ff.atempo(str(tone_with_silence), 1.10, out)
        assert ff.duration(out) == pytest.approx(ff.duration(tone_with_silence) / 1.10, abs=0.06)

    def test_放慢后变长(self, tone_with_silence, tmp_path):
        out = str(tmp_path / "slow.wav")
        ff.atempo(str(tone_with_silence), 0.95, out)
        assert ff.duration(out) == pytest.approx(ff.duration(tone_with_silence) / 0.95, abs=0.06)


class TestWrapAudioAsVideo:
    def test_纯音频补出可用的视频轨(self, tmp_path):
        audio = str(tmp_path / "voice.wav")
        ff._run(["-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=2.5",
                 "-ac", "1", "-ar", "44100", audio])
        out = str(tmp_path / "wrapped.mp4")
        ff.wrap_audio_as_video(audio, out, ff.duration(audio))
        assert ff.has_video(out) is True
        assert ff.duration(out) == pytest.approx(2.5, abs=0.2)
