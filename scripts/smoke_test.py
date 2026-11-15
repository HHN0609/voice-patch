"""端到端冒烟测试：mock provider 跑通全链路（不依赖任何云 Key）。

步骤：造测试视频 → 上传抽轨 → 转写（区间扩展）→ 改字合成（多 take 择优）
→ 拼接/底噪/响度 → 换轨导出 → 校验总时长不变（等长替换）。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import orchestrator as orc
from backend.media import ffmpeg_ops as ff

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / "data" / "_smoke"
TMP.mkdir(parents=True, exist_ok=True)


def main() -> None:
    t_start = time.time()

    # [1] 造测试视频（6s 彩条 + 440Hz 正弦）
    video = str(TMP / "test.mp4")
    ff.make_test_media(video, 6.0)
    print(f"[1] 测试视频就绪: {video}")

    # [2] 上传 + 抽轨
    proj = orc.create_project("smoke", video)
    pid = proj["id"]
    print(f"[2] 项目 {pid} · 原始时长 {proj['duration']:.3f}s")

    # [3] 转写（mock ASR 返回空 → 手动文本路径）
    seg = orc.transcribe_segment(pid, 2.0, 3.2)
    sid = seg["segment_id"]
    print(f"[3] 选区 [2.0,3.2] → 扩展 [{seg['T0']},{seg['T1']}] · 窗口 {seg['window']}s")

    # [4] 改字合成：模拟“一千 → 1999.99 元”
    synth = orc.synthesize_segment(sid, "1999.99元", allow_stretch=False)
    print(f"[4] 语速 {synth['speed']}× · 窗口 {synth['window']}s · overflow={synth['overflow']} "
          f"· d_class={synth['d_class']} · delta={synth['delta']}s")
    best = min(synth["takes"], key=lambda t: abs(t["delta"]))
    print(f"    选中 take{best['take_no']}: final={best['final_dur']}s Δ={best['delta']}s")

    # [5] 渲染导出
    out = orc.render_segment(sid, best["take_id"])
    final_video = str(ROOT / out["video"].lstrip("/").replace("data/", "data/", 1))
    final_video = str((ROOT / out["video"].lstrip("/")).resolve())
    d_new = ff.duration(final_video)
    d_orig = proj["duration"]
    drift = abs(d_new - d_orig)
    print(f"[5] 导出 {out['video']} · 原始 {d_orig:.3f}s → 成片 {d_new:.3f}s · 漂移 {drift * 1000:.1f}ms")

    assert Path(final_video).exists(), "成片不存在"
    assert drift < 0.05, f"等长替换失败：漂移 {drift:.3f}s 超过 50ms"
    print(f"\nPASS ✅ 全链路跑通，总耗时 {time.time() - t_start:.1f}s")


if __name__ == "__main__":
    main()
