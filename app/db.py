"""SQLite 存储：projects / segments / takes 三张表。"""
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .config import CFG

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY,
  name TEXT,
  video_path TEXT,
  audio_path TEXT,
  duration REAL,
  voice_id TEXT,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS segments(
  id TEXT PRIMARY KEY,
  project_id TEXT,
  t0_sel REAL, t1_sel REAL,  -- 用户框选（SQLite 列名不区分大小写，不能用 T0/T1 同名）
  T0 REAL, T1 REAL,          -- 自动扩展后的重录区间
  text_raw TEXT,             -- ASR 原文
  text_norm TEXT,            -- 规整后（可被用户覆盖）
  status TEXT,               -- transcribed / synthesized / rendered
  created_at REAL
);
CREATE TABLE IF NOT EXISTS takes(
  id TEXT PRIMARY KEY,
  segment_id TEXT,
  take_no INTEGER,
  text_norm TEXT,
  provider TEXT,
  voice_id TEXT,
  speed REAL,
  seed INTEGER,
  wav_path TEXT,
  raw_dur REAL,              -- 合成原始时长
  final_dur REAL,            -- 适配后时长
  stretch REAL,              -- time-stretch 系数（1.0 = 未拉伸）
  status TEXT,               -- ok / overflow
  delta REAL,                -- final_dur - W
  selected INTEGER DEFAULT 0,
  created_at REAL
);
"""


def conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        db_path = Path(CFG["storage"]["data_dir"]) / "voicepatch.db"
        _CONN = sqlite3.connect(db_path, check_same_thread=False)
        _CONN.row_factory = sqlite3.Row
        _CONN.executescript(_SCHEMA)
        _CONN.commit()
    return _CONN


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()


def execute(sql: str, params: tuple = ()) -> None:
    with _LOCK:
        c = conn()
        c.execute(sql, params)
        c.commit()


def query_all(sql: str, params: tuple = ()) -> list[dict]:
    with _LOCK:
        rows = conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query_all(sql, params)
    return rows[0] if rows else None
