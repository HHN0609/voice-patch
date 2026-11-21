"""Web 层：FastAPI 路由。运行：python run.py 或 uvicorn backend.main:app"""
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import orchestrator as orc
from .config import CFG
from .db import query_all

app = FastAPI(title="voice-patch 口误修复")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

_DATA = Path(CFG["storage"]["data_dir"])
app.mount("/data", StaticFiles(directory=str(_DATA)), name="data")
app.mount("/ui", StaticFiles(directory=str(Path(CFG["root"]) / "web"), html=True), name="ui")


@app.get("/api/health")
def health() -> dict:
    from .llm import decider
    from .providers import registry
    return {"ok": True, "warnings": registry.warnings(), "llm": decider.status()}


@app.post("/api/projects")
async def upload(file: UploadFile = File(...), name: str = "") -> dict:
    suffix = Path(file.filename or "media.mp4").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        # 默认用完整文件名（含后缀），前端直接展示即可
        return orc.create_project(name or Path(file.filename or "project").name, tmp_path)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/projects")
def list_projects() -> list[dict]:
    # 复用 get_project：把 has_video 统一成布尔、并补上 video_url/audio_url
    return [orc.get_project(p["id"]) for p in query_all("SELECT * FROM projects ORDER BY created_at DESC")]


@app.get("/api/projects/{pid}/audio.wav")
def project_audio(pid: str) -> FileResponse:
    p = orc.get_project(pid)
    return FileResponse(p["audio_path"], media_type="audio/wav")


class TranscribeReq(BaseModel):
    t0: float
    t1: float


@app.post("/api/projects/{pid}/transcribe")
def transcribe(pid: str, req: TranscribeReq) -> dict:
    if req.t1 - req.t0 < 0.1:
        raise HTTPException(400, "区间太短（<0.1s）")
    try:
        return orc.transcribe_segment(pid, req.t0, req.t1)
    except Exception as e:
        raise HTTPException(500, str(e))


class SynthesizeReq(BaseModel):
    text: str
    speed: float | None = None          # None = 自动求最优；UI 滑块可覆盖
    takes: int | None = None
    ref_t0: float | None = None         # 用户选中的参考音色区间（同说话人）
    ref_t1: float | None = None
    use_llm: bool | None = None         # True/False 覆盖配置；None 跟随 config
    # 用户“夺回语速控制权”时置 True：AI 仍参与静音分配与文本精简，但不改语速
    llm_speed_locked: bool = False


@app.post("/api/segments/{sid}/synthesize")
def synthesize(sid: str, req: SynthesizeReq) -> dict:
    if not req.text.strip():
        raise HTTPException(400, "文本为空")
    try:
        return orc.synthesize_segment(sid, req.text, req.speed, req.takes,
                                      req.ref_t0, req.ref_t1, req.use_llm,
                                      req.llm_speed_locked)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/projects/{pid}")
def project_detail(pid: str) -> dict:
    try:
        return orc.get_project(pid)
    except KeyError as e:
        raise HTTPException(404, str(e))


class RenderReq(BaseModel):
    take_id: str


@app.post("/api/segments/{sid}/render")
def render(sid: str, req: RenderReq) -> dict:
    try:
        return orc.render_segment(sid, req.take_id)
    except Exception as e:
        raise HTTPException(500, str(e))
