"""火山引擎·录音文件识别（大模型/录音文件识别）客户端。

⚠️ 接口路径与字段以官网文档为准（不同版本差异较大），开通服务后请对照
《录音文件识别 API 文档》核对 host / 路径 / cluster / 返回字段解析三处。
"""
import base64
import json
import time

import httpx

from ..config import CFG
from .base import Word

HOST = "https://openspeech.bytedance.com"


class VolcASR:
    name = "volc"

    def __init__(self) -> None:
        v = CFG["volc"]
        self.appid = v.get("appid") or ""
        self.token = v.get("access_token") or ""
        self.cluster = v.get("asr_cluster") or ""
        if not (self.appid and self.token and self.cluster):
            raise RuntimeError("火山 ASR 未配置：请填 config.yaml [volc] 或设置 "
                               "VOLC_APP_ID / VOLC_ACCESS_TOKEN / volc.asr_cluster")

    # -- 内部：提交 + 轮询 -------------------------------------------------
    def _submit(self, wav_bytes: bytes) -> str:
        payload = {
            "app": {"appid": self.appid, "cluster": self.cluster},
            "request": {"model_name": "bigmodel", "enable_punctuation": True,
                        "enable_itn": True, "show_utterances": True},
            "audio_data": base64.b64encode(wav_bytes).decode(),
        }
        r = httpx.post(f"{HOST}/api/v1/auc/submit",
                       headers={"Authorization": f"Bearer; {self.token}"},
                       json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("code") not in (0, "0", None):
            raise RuntimeError(f"火山 ASR submit 失败: {data}")
        return data["id"]

    def _query(self, task_id: str) -> dict:
        for _ in range(60):
            r = httpx.get(f"{HOST}/api/v1/auc",
                          params={"appid": self.appid, "token": self.token, "id": task_id},
                          headers={"Authorization": f"Bearer; {self.token}"}, timeout=30)
            r.raise_for_status()
            data = r.json()
            if data.get("code") in (0, "0", None) and data.get("status") in (3, "3"):
                return data
            time.sleep(1.0)
        raise TimeoutError("火山 ASR 查询超时")

    # -- 对外 ---------------------------------------------------------------
    def transcribe(self, wav_path: str, lang: str = "zh",
                   word_timestamps: bool = True) -> list[Word]:
        with open(wav_path, "rb") as f:
            wav_bytes = f.read()
        task_id = self._submit(wav_bytes)
        data = self._query(task_id)
        result = data.get("result", {})
        words: list[Word] = []
        for utt in result.get("utterances", []):
            u_start = utt.get("start_time", 0) / 1000.0
            ws = utt.get("words") or []
            if ws:
                for w in ws:
                    words.append(Word(text=w.get("text", ""),
                                      start=w.get("start_time", 0) / 1000.0,
                                      end=w.get("end_time", 0) / 1000.0,
                                      confidence=float(w.get("confidence", 1.0))))
            else:
                # 无词级时间戳时退化为句级
                words.append(Word(text=utt.get("text", ""), start=u_start,
                                  end=utt.get("end_time", 0) / 1000.0, confidence=1.0))
        if not words and result.get("text"):
            words = [Word(text=result["text"], start=0.0,
                          end=json.loads(json.dumps(data)).get("duration", 0) / 1000.0
                          if isinstance(data.get("duration"), (int, float)) else 0.0,
                          confidence=1.0)]
        return words
