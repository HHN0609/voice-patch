# backend/ · 后端

FastAPI 服务，负责视频抽轨、ASR 转写、TTS 合成、时长适配、ffmpeg 渲染与 SQLite 落库。前端静态产物由本服务在 `/ui` 提供，运行时数据在 `/data`。

## 启动

```powershell
python run.py          # uvicorn backend.main:app，127.0.0.1:8765
```

也可以直接用 uvicorn：

```powershell
uvicorn backend.main:app --host 127.0.0.1 --port 8765 --reload
```

> 改了 `config.yaml` / `.env` 后需要重启进程：配置与 provider 实例都在进程内缓存。

## 模块

| 文件 | 职责 |
| --- | --- |
| `main.py` | 路由层，只做参数校验与转发；挂载 `/data`（数据目录）与 `/ui`（`web/` 构建产物） |
| `orchestrator.py` | 编排层，10 步流水线的唯一入口，每步落库、可重入 |
| `config.py` | 配置加载：系统环境变量 > `.env` > `config.yaml`；补全 `root` 与 `storage.data_dir` 绝对路径 |
| `db.py` | SQLite（`data/voicepatch.db`），三张表 `projects` / `segments` / `takes` |
| `providers/` | ASR / TTS 抽象与实现，见下节 |
| `media/ffmpeg_ops.py` | ffmpeg/ffprobe 纯函数封装：探测、抽轨、裁切、静音检测、变速、交叉淡化、底噪混合、响度测量与应用、换轨封装 |
| `audio/fitting.py` | 时长适配链：`auto_speed` 求语速、`fit_take` 单 take 适配、`pick_best` 择优 |
| `text/normalize.py` | 文本规整：数字 / 百分比 / 年份 → 中文读法；`estimate_duration_zh` 粗估时长 |
| `pan_upload.py` | `pan.ezv.cc` 分片上传 + 审核轮询 + 匿名 URL 验证 + 回收，供标准版 ASR 拉取音频 |

## 流水线（orchestrator）

| 步骤 | 函数 | 说明 |
| --- | --- | --- |
| [1] | `create_project` | 保存上传视频 → `ffprobe` 取时长 → 抽 48kHz 双声道 WAV → 写 `projects` |
| [2][3] | `expand_region` | 用 `silencedetect` 找静音，把选区向外扩到最近静音边界（受 `region_expand_max` 限制） |
| [4] | `transcribe_segment` | 裁切选区音频 → ASR → 规整文本 → 写 `segments`（保存用户框选与扩展后窗口） |
| [5]~[8] | `synthesize_segment` | 参考音频准备（用户框选 / RMS 自动挑选）→ 克隆 → 探时长求语速 → 生成 N 个 take → 变速对齐 → 写 `takes` |
| [9][10] | `render_segment` | 切头/尾 → 等功率交叉淡化拼接 → 底噪垫床 → 响度测量+归一 → 换轨封装 `patch_final.mp4` |

`synthesize_segment` 返回值里两个关键标记：

- `overflow`：最佳 take 仍超出容忍窗口。
- `d_class`：`Δ > 窗口长度 × 0.5`，说明新文本相对窗口膨胀太多，前端提示精简文本。

## Provider 抽象

协议在 `providers/base.py`：

```python
class ASRProvider(Protocol):
    def transcribe(self, wav_path: str, lang="zh", word_timestamps=True) -> list[Word]: ...

class TTSProvider(Protocol):
    def clone(self, ref_audio: str, ref_text: str | None = None) -> str: ...
    def synthesize(self, text: str, voice_id: str, speed=1.0, seed=None) -> SynthResult: ...
```

- `Word`：`{text, start, end, confidence}`。ASR **必须返回字/词字典列表**，返回 `(text, words)` 之类的元组会让编排层迭代失败。
- `SynthResult`：`{wav_path, duration, model_version, seed}`，`duration` 需为实际文件时长。

现有实现：

| provider | 说明 |
| --- | --- |
| `mock` | `MockASR` 返回空列表（前端据此提示手动输入）；`MockTTS` 生成与字数相关的正弦音，时长带随机抖动，用于无 Key 冒烟 |
| `deepseek` | 仅 ASR：`POST /v1/tools/asr` 同步上传（转 16k 单声道 PCM WAV），返回 `text` + `duration_ms`；**无字级时间戳**，词级时间按字符数均分估算。单次 ≤120s、≤8MiB |
| `volc` | ASR：标准版录音文件识别（submit → 轮询 query），音频经 `pan.ezv.cc` 换成公开 URL，解析 `utterances[].words` 毫秒时间戳；TTS：`seed-audio-1.0`，参考音频 base64 随请求携带，`speech_rate=(speed-1)*100` |

新增实现：在 `providers/` 加文件实现上述两个方法，再到 `providers/registry.py` 的 `_instantiate` 里注册名字，最后改 `config.yaml` 的 `provider`。初始化抛异常会按 `fallback` 自动回落并在 `/api/health` 的 `warnings` 里提示。

鉴权：优先 `X-Api-Key` 单头（新版控制台）；`appid` + `access_token` 同时存在时走旧版双头。

## 数据模型

```
projects(id, name, video_path, audio_path, duration, voice_id, created_at)
segments(id, project_id, t0_sel, t1_sel, T0, T1, text_raw, text_norm, status, created_at)
takes(id, segment_id, take_no, text_norm, provider, voice_id, speed, seed,
      wav_path, raw_dur, final_dur, stretch, status, delta, selected, created_at)
```

- `t0_sel/t1_sel` 是用户框选，`T0/T1` 是静音扩展后的实际重录窗口。
- `status`：`transcribed` → `synthesized` → `rendered`；take 的 `status` 为 `ok` / `overflow`。
- `selected=1` 表示最终出片使用的候选。
- SQLite 列名不区分大小写，所以框选列加了 `_sel` 后缀。

## ffmpeg 依赖

`media/ffmpeg_ops.resolve_bins()` 的查找顺序：

1. `PATH` 中的 `ffmpeg` / `ffprobe`
2. `imageio_ffmpeg` wheel 自带二进制（离线可用，默认路径）
3. `static_ffmpeg`（需联网下载，仅当设置环境变量 `VOICE_PATCH_STATIC_FFMPEG=1` 时启用）

没有 `ffprobe` 时会退化为解析 `ffmpeg -i` 的 stderr 取时长与采样率。

## 注意

- 单个服务实例即可；SQLite 连接由模块级锁串行化写入。
- 渲染只替换音轨：`-map 0:v:0 -map 1:a:0 -c:v copy -c:a aac`，画面不重编码。
- `/data` 与 `/ui` 都是静态挂载，新增前端资源需要重新 `npm run build` 生成到 `web/`。
