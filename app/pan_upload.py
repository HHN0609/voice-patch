"""pan.ezv.cc 中转上传：本地音频 → 公开可访问 URL（供火山标准版 ASR submit 使用）。

流程（已实测）：
1. POST /api/pan/uploads 创建分片会话（返回 chunkBytes，单文件通常一片搞定）
2. PUT /api/pan/uploads/{id}/parts/{index} 逐片上传
3. POST /api/pan/uploads/{id}/complete 完成（进入 AI 审核，实测约 10~15 秒）
4. 轮询 POST /pan/api?action=links（hashes=文件ID）直到 securityStatus=normal
5. 返回匿名可下载的 https://pan.ezv.cc/d/{fileId}（实测无需任何鉴权）

额度：免费空间内 chargedCents=0；maxOverageCents 固定 0（不产生超额扣费）。
"""
import time
from pathlib import Path

import httpx

from .config import CFG


class PanError(RuntimeError):
    pass


def _cfg() -> tuple[str, str]:
    pan = CFG.get("pan") or {}
    key = pan.get("api_key") or ""
    if not key:
        raise PanError("未配置中转网盘：请在 .env 填 PAN_API_KEY")
    return pan.get("base_url") or "https://pan.ezv.cc", key


def _req(client: httpx.Client, method: str, url: str, **kw):
    last = None
    for i in range(4):  # 该服务偶发 TLS 握手超时，重试即可
        try:
            return client.request(method, url, timeout=60, **kw)
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise PanError(f"网盘请求失败（已重试 4 次）: {url} · {type(last).__name__}")


def upload_for_url(path: str, poll_seconds: int = 90) -> str:
    """上传音频文件，阻塞至审核通过，返回匿名可访问的下载 URL。"""
    base, key = _cfg()
    H = {"Authorization": f"Bearer {key}"}
    p = Path(path)
    data = p.read_bytes()

    with httpx.Client() as c:
        r = _req(c, "POST", f"{base}/api/pan/uploads", headers=H,
                 json={"name": p.name, "size": len(data), "maxOverageCents": 0})
        if r.status_code != 200:
            raise PanError(f"创建上传会话失败: HTTP {r.status_code} {r.text[:200]}")
        sess = (r.json() or {}).get("data") or {}
        upload_id = sess.get("uploadId")
        chunk = sess.get("chunkBytes") or len(data)
        if not upload_id:
            raise PanError(f"上传会话缺少 uploadId: {r.text[:200]}")

        for idx, off in enumerate(range(0, len(data), chunk)):
            pr = _req(c, "PUT", f"{base}/api/pan/uploads/{upload_id}/parts/{idx}",
                      headers={**H, "Content-Type": "application/octet-stream"},
                      content=data[off:off + chunk])
            if pr.status_code != 200:
                raise PanError(f"分片 {idx} 上传失败: HTTP {pr.status_code} {pr.text[:200]}")

        cr = _req(c, "POST", f"{base}/api/pan/uploads/{upload_id}/complete",
                  headers=H, json={})
        if cr.status_code != 200:
            raise PanError(f"完成上传失败: HTTP {cr.status_code} {cr.text[:200]}")
        cj = (cr.json() or {}).get("data") or {}
        file_id = cj.get("fileId")
        if not file_id:
            raise PanError(f"完成响应缺少 fileId: {cr.text[:200]}")

        # 轮询 AI 审核结果
        deadline = time.time() + poll_seconds
        status = ""
        while time.time() < deadline:
            time.sleep(4)
            lr = _req(c, "POST", f"{base}/pan/api?action=links", headers=H,
                      json={"hashes": file_id})
            info = ((lr.json() or {}).get("data") or {}) if lr.status_code == 200 else {}
            status = info.get("securityStatus", "")
            if status == "normal":
                break
            if status in ("blocked", "rejected", "violation"):
                raise PanError(f"网盘审核未通过（{status}），文件无法公开访问")
        if status != "normal":
            raise PanError(f"网盘审核超时（{poll_seconds}s），当前状态: {status or '未知'}")

        # 终验：确认匿名可下载（火山服务器不带鉴权来拉）
        url = f"{base}/d/{file_id}"
        check = _req(c, "GET", url)
        if check.status_code != 200:
            raise PanError(f"匿名下载验证失败: HTTP {check.status_code}（火山将无法拉取）")
        return url


def trash(file_id: str) -> None:
    """转写完成后回收文件，避免占用网盘空间。失败静默（不影响主流程）。"""
    try:
        base, key = _cfg()
        with httpx.Client() as c:
            _req(c, "POST", f"{base}/api/pan/files/batch",
                 headers={"Authorization": f"Bearer {key}"},
                 json={"action": "trash", "fileIds": [file_id]})
    except Exception:
        pass
