"""编排层：选区文本裁剪、参考窗口、项目创建（视频 / 纯音频）、库迁移。"""
import sqlite3

import pytest

from backend import db
from backend import orchestrator as orc
from backend.media import ffmpeg_ops as ff


class TestTrimWords:
    WORDS = [
        {"text": "A", "start": 0.0, "end": 0.5},
        {"text": "B", "start": 0.5, "end": 1.0},
        {"text": "C", "start": 1.2, "end": 1.6},
    ]

    def test_舍掉选区终点之后的词(self):
        kept = orc.trim_words(self.WORDS, 1.1)
        assert [w["text"] for w in kept] == ["A", "B"]

    def test_终点覆盖到该词时保留(self):
        # C 的中点 1.4，终点 1.45 时保留
        kept = orc.trim_words(self.WORDS, 1.45)
        assert [w["text"] for w in kept] == ["A", "B", "C"]

    def test_容差内保留(self):
        kept = orc.trim_words(self.WORDS, 1.39)
        assert "C" in [w["text"] for w in kept]

    def test_空列表安全(self):
        assert orc.trim_words([], 1.0) == []


class TestRefWindow:
    def test_足够长时原样返回(self):
        assert orc.ref_window(1.0, 4.0, 10.0) == (1.0, 4.0)

    def test_不足时向两侧补足且居中(self):
        r0, r1 = orc.ref_window(5.0, 6.0, 10.0)
        assert r1 - r0 == pytest.approx(2.5, abs=1e-6)
        assert (r0 + r1) / 2 == pytest.approx(5.5, abs=1e-6)

    def test_不越过音频起点(self):
        r0, r1 = orc.ref_window(0.0, 1.0, 10.0)
        assert r0 == 0.0
        assert r1 == pytest.approx(2.5, abs=1e-6)

    def test_不越过音频终点(self):
        r0, r1 = orc.ref_window(9.5, 10.0, 10.0)
        assert r1 == pytest.approx(10.0, abs=1e-6)
        assert r1 - r0 == pytest.approx(2.5, abs=1e-6)


@pytest.fixture()
def media_dir(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    return d


class TestCreateProject:
    def test_视频上传保留后缀并标记has_video(self, tmp_data, media_dir):
        src = str(media_dir / "sample.mp4")
        ff.make_test_media(src, 3.0)
        proj = orc.create_project("sample.mp4", src)

        assert proj["name"] == "sample.mp4"
        assert proj["has_video"] is True
        assert proj["video_url"].endswith("source.mp4")
        assert proj["duration"] == pytest.approx(3.0, abs=0.3)

    def test_纯音频上传补占位画面(self, tmp_data, media_dir):
        src = str(media_dir / "voice.mp3")
        ff._run(["-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=4",
                 "-ac", "1", "-ar", "44100", src])
        proj = orc.create_project("voice.mp3", src)

        assert proj["name"] == "voice.mp3"
        assert proj["has_video"] is False
        assert proj["video_url"].endswith("source.mp4")
        assert proj["duration"] == pytest.approx(4.0, abs=0.3)

        pdir = tmp_data / "projects" / proj["id"]
        assert ff.has_video(str(pdir / "source.mp4")) is True
        # 原始音频已被占位视频取代，避免重复占空间
        assert not (pdir / "voice.mp3").exists()
        assert (pdir / "audio_48k.wav").exists()

    def test_回读项目保留has_video标记(self, tmp_data, media_dir):
        src = str(media_dir / "clip.mp4")
        ff.make_test_media(src, 2.0)
        proj = orc.create_project("clip.mp4", src)

        back = orc.get_project(proj["id"])
        assert back["has_video"] is True
        assert back["video_url"].endswith("source.mp4")
        assert back["audio_url"].endswith("audio_48k.wav")


class TestMigration:
    def test_老库自动补上has_video列(self, tmp_path):
        """历史库是 7 列，打开时应自动 ALTER 出 has_video。"""
        from backend.config import CFG

        dbfile = tmp_path / "voicepatch.db"
        c = sqlite3.connect(dbfile)
        c.execute("CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT, video_path TEXT,"
                  " audio_path TEXT, duration REAL, voice_id TEXT, created_at REAL)")
        c.commit()
        c.close()

        prev_dir, prev_conn = CFG["storage"]["data_dir"], db._CONN
        db._CONN = None
        CFG["storage"]["data_dir"] = str(tmp_path)
        try:
            cols = {r["name"] for r in db.conn().execute("PRAGMA table_info(projects)")}
            assert "has_video" in cols
            db.execute("INSERT INTO projects(id,name,video_path,audio_path,duration,voice_id,created_at)"
                       " VALUES('p1','n','v','a',1.0,NULL,0.0)")
            got = db.query_one("SELECT has_video FROM projects WHERE id='p1'")
            assert got["has_video"] == 1  # 老数据默认按视频处理
        finally:
            db._CONN = None
            CFG["storage"]["data_dir"] = prev_dir
            db._CONN = prev_conn
