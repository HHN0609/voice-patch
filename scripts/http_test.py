"""HTTP 链路测试：upload → transcribe → synthesize → render → download → UI。"""
import io
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8765"


def main() -> None:
    # 准备测试视频（若已被上次测试移走则重新生成）
    test_video = ROOT / "data" / "_smoke" / "http_test.mp4"
    test_video.parent.mkdir(parents=True, exist_ok=True)
    if not test_video.exists():
        sys.path.insert(0, str(ROOT))
        from app.media import ffmpeg_ops as ff
        ff.make_test_media(str(test_video), 6.0)

    c = httpx.Client(base_url=BASE, timeout=120)

    h = c.get("/api/health").json()
    print(f"[health] {h}")

    with open(test_video, "rb") as f:
        up = c.post("/api/projects", files={"file": ("http_test.mp4", f, "video/mp4")}).json()
    print(f"[upload] id={up['id']} dur={up['duration']}")
    pid = up["id"]

    tr = c.post(f"/api/projects/{pid}/transcribe", json={"t0": 2.0, "t1": 3.2}).json()
    print(f"[transcribe] sid={tr['segment_id']} 扩展=[{tr['T0']},{tr['T1']}] 窗口={tr['window']}s")
    sid = tr["segment_id"]

    sy = c.post(f"/api/segments/{sid}/synthesize",
                json={"text": "1999.99元", "allow_stretch": True, "takes": 2}).json()
    print(f"[synthesize] speed={sy['speed']} overflow={sy['overflow']} "
          f"d_class={sy['d_class']} takes={[(t['take_no'], t['final_dur'], t['status']) for t in sy['takes']]}")

    rd = c.post(f"/api/segments/{sid}/render", json={"take_id": sy["takes"][0]["take_id"]}).json()
    print(f"[render] {rd}")

    v = c.get(rd["video"])
    print(f"[download] {v.status_code} {len(v.content)} bytes")
    assert v.status_code == 200 and len(v.content) > 10000

    ui = c.get("/ui/")
    print(f"[ui] {ui.status_code} ({len(ui.text)} bytes, wavesurfer={'WaveSurfer' in ui.text})")
    assert ui.status_code == 200

    wav = c.get(f"/api/projects/{pid}/audio.wav")
    print(f"[audio] {wav.status_code} {len(wav.content)} bytes")

    print("\nPASS ✅ HTTP 全链路 + 前端页面就绪")


if __name__ == "__main__":
    main()
