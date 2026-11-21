"""后端测试公共设置：路径注入 + 数据目录隔离（不污染真实 data/）。

运行：.venv\\Scripts\\python.exe -m pytest tests -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def tmp_data(tmp_path):
    """把配置里的数据目录指向临时目录，并重置 SQLite 连接。"""
    from backend import db
    from backend.config import CFG

    prev_dir = CFG["storage"]["data_dir"]
    prev_conn = db._CONN
    db._CONN = None
    CFG["storage"]["data_dir"] = str(tmp_path)
    try:
        yield tmp_path
    finally:
        db._CONN = None
        CFG["storage"]["data_dir"] = prev_dir
        if prev_conn is not None:
            prev_conn.close()


@pytest.fixture()
def mock_providers(monkeypatch):
    """强制 ASR / TTS 走 mock，避免测试触网。"""
    from backend.providers import registry
    from backend.providers.mock import MockASR, MockTTS

    monkeypatch.setattr(registry, "get_asr", lambda: MockASR())
    monkeypatch.setattr(registry, "get_tts", lambda: MockTTS())
