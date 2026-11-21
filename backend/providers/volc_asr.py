"""火山引擎·豆包录音文件识别（标准版，异步 submit/query）客户端。

文档：https://docs.volcengine.com/docs/doubao-voice/task-submission-http-1
鉴权：X-Api-Key 单头（同 TTS），资源 ID volc.seedasr.auc（2.0 模型）。
音频通道：本地文件 → pan.ezv.cc 中转拿到公开 URL → submit → 轮询 query。
返回字级时间戳（utterances[].words，毫秒）。
"""
import time
import uuid
from pathlib import Path

import httpx

from .. import pan_upload
from ..config import CFG
from .base import Word

BASE = "https://openspeech.bytedance.com/api/v3/auc/bigmodel"
RESOURCE_ID = "volc.seedasr.auc"


class VolcASR:
    name = "volc"

    def __init__(self) -> None:
        v = CFG["volc"]
        self.api_key = v.get("api_key") or ""
        if not self.api_key:
            raise RuntimeError("火山 ASR 未配置：请在 .env 填 VOLC_API_KEY（新版控制台 API Key）")

    def _headers(self, task_id: str) -> dict:
        return {"Content-Type": "application/json",
                "X-Api-Key": self.api_key,
                "X-Api-Resource-Id": RESOURCE_ID,
                "X-Api-Request-Id": task_id,
                "X-Api-Sequence": "-1"}

    def transcribe(self, wav_path: str, lang: str = "zh",
                   word_timestamps: bool = True) -> list[Word]:
        audio_url = pan_upload.upload_for_url(wav_path)
        task_id = uuid.uuid4().hex
        r = httpx.post(f"{BASE}/submit", headers=self._headers(task_id),
                       json={"audio": {"url": audio_url, "format": "wav"},
                             "show_utterances": True}, timeout=120)
        status = r.headers.get("X-Api-Status-Code", "")
        if status != "20000000":
            raise RuntimeError(f"火山 ASR 提交失败: status={status} "
                               f"message={r.headers.get('X-Api-Message')} body={r.text[:300]}")

        # 轮询结果（短音频通常十几秒内完成）
        deadline = time.time() + 180
        while time.time() < deadline:
            time.sleep(3)
            q = httpx.post(f"{BASE}/query", headers=self._headers(task_id),
                           json={}, timeout=60)
            qs = q.headers.get("X-Api-Status-Code", "")
            body = q.json() if q.status_code == 200 else {}
            result = body.get("result")
            if qs == "20000000" and result and result.get("text"):
                pan_upload.trash(_current_file_id(audio_url))
                return self._parse(result)
            if qs in ("20000001", "20000003"):  # 处理中（20000001=Processing，实测）
                continue
            if qs and qs != "20000000":
                raise RuntimeError(f"火山 ASR 查询失败: status={qs} "
                                   f"message={q.headers.get('X-Api-Message')} body={q.text[:300]}")
        raise RuntimeError("火山 ASR 轮询超时（180s），任务可能仍在排队，请稍后重试")

    @staticmethod
    def _parse(result: dict) -> list[Word]:
        words: list[Word] = []
        for utt in result.get("utterances") or []:
            for w in utt.get("words") or []:
                if w.get("text"):
                    words.append(Word(text=w["text"],
                                      start=(w.get("start_time") or 0) / 1000.0,
                                      end=(w.get("end_time") or 0) / 1000.0,
                                      confidence=float(w.get("confidence") or 0.0)))
        return words


def _current_file_id(audio_url: str) -> str:
    return audio_url.rsplit("/", 1)[-1]
