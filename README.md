# voice-patch · 口误修复

上传一段含口误的视频，在波形上框住说错的那一小段，改掉文字，用**同一个人的音色**重新合成，再**等长**拼回原视频——成片画面不重编码、总时长基本不变。

```
上传视频 → 框选口误区间 → ASR 转写 → 改文本 → 克隆音色多 take 合成 → 试听择优 → 拼回视频导出
```

## 功能

- **波形选区**：鼠标拖拽框选口误区间；替换区间与框选区间严格一致，不会动到没框到的音频。
- **区间转写**：默认 DeepSeek 语音识别（同步上传、整段文本）；该接口不返回字级时间戳，字位置按时长均分估算，点字为近似跳转。可切回 `volc` 取真实字级时间戳。
- **文本规整**：`1999.99元`、`35%`、`2026年` 等自动转成 TTS 稳定的中文读法。
- **时长适配**：按选区内实际语音跨度适配（不是按框选总长）→ 语速在 `0.95~1.10` 内先吸收差额 → 仍不够时，语音偏长就占用选区自带的静音、偏短就多垫静音（都优先尾部）→ 一次生成 3 个 take 择优。语速与静音缓冲都用满仍塞不下才提示溢出。
- **音色一致**：不框参考音时默认直接用口误区间那段原声（不足 2.5s 自动向两侧补足）；多人视频可手动框选“该说话人”的一段话，强制按此音色克隆。
- **无缝渲染**：头/新音频/尾等功率交叉淡化 → 补底噪垫床 → EBU R128 响度归一 → 仅替换音轨（视频流 copy）。
- **零 Key 可跑**：`mock` provider 可在没有任何云服务密钥时跑通全链路，便于联调。
- **AI 决策（可选）**：打开「AI 决策」后，语速、优先占用哪侧静音、溢出时的精简改写建议交给 DeepSeek；语速可按「改由我控制」单独夺回（保留 AI 的静音与文本决策）。所有输出都有硬边界（越界夹紧、类型不符丢弃），无 Key / 超时 / 坏 JSON 一律静默回退内置规则，不影响出片。
- **Web UI**：React + wavesurfer.js，支持视频画面与波形联动、take 试听、成片预览与下载。

## 架构

```
┌──────────────────────────────┐        ┌────────────────────────────────────┐
│ frontend/  React + TS + Vite │        │ backend/  FastAPI（127.0.0.1:8765）  │
│  · 波形选区 / 试听 / 预览     │──/api─▶│  · orchestrator 10 步流水线         │
└──────────────────────────────┘        │  · providers  ASR / TTS（可替换）   │
             │ build                    │  · media      ffmpeg 封装            │
             ▼                          │  · audio      时长适配               │
        web/  ──────── /ui ────────────▶│  · text       数字/百分比规整        │
                                        │  · db         SQLite                │
                                        └────────────────────────────────────┘
                                                     │
                                             data/（视频、中间音频、成片、db）
```

- 生产/日常使用：`python run.py` 起单进程，前端静态产物由 FastAPI 挂在 `/ui`，数据文件挂在 `/data`。
- 开发前端：`npm run dev` 起 Vite（5173），`/api`、`/data` 反向代理到 8765，实现前后端分离联调。

## 快速开始

### 环境要求

| 依赖 | 说明 |
| --- | --- |
| Python 3.10+ | 后端 |
| Node.js 18+ | 首次构建前端、以及修改 UI 时需要 |
| ffmpeg | 优先用 `PATH` 里的；没有则用 `imageio-ffmpeg` wheel 自带的二进制 |

### 1. 构建前端（首次或前端改动后）

`web/` 是构建产物、不入库，因此需要本地构建一次：

```powershell
cd frontend
npm install
npm run build    # 产物输出到 ../web
cd ..
```

### 2. 后端

```powershell
cd voice-patch
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env    # 填写密钥，见下文
python run.py
```

打开 <http://127.0.0.1:8765/ui/>。健康检查：`GET /api/health` 会返回当前 provider 的降级告警。

### 3. 前端开发模式（可选）

```powershell
cd frontend
npm install
npm run dev      # http://127.0.0.1:5173 ，热更新开发
npm run build    # 产物输出到 ../web ，随后刷新 /ui/ 即可看到最新界面
```

> `web/` 是构建产物，不要手改；要改界面请改 `frontend/src`。

## 配置

配置优先级：**系统环境变量 > `.env` > `config.yaml`**。

### .env（不入库，模板见 `.env.example`）

| 变量 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek API Key，默认 ASR provider 使用 |
| `VOLC_API_KEY` | 火山引擎新版控制台 API Key（`X-Api-Key` 单头鉴权，推荐），TTS 必填；切回 volc ASR 时也用它 |
| `VOLC_APP_ID` / `VOLC_ACCESS_TOKEN` | 旧版控制台双头鉴权（可选） |
| `PAN_API_KEY` | 文件中转网盘 `pan.ezv.cc` 的 Key：仅 `volc` ASR 需要公开 URL，本地音频先传这里中转 |

### config.yaml

| 配置 | 说明 |
| --- | --- |
| `asr.provider` / `tts.provider` | ASR：`deepseek`（默认）/ `volc` / `mock`；TTS：`volc` / `mock` |
| `asr.fallback` / `tts.fallback` | provider 初始化失败时回落的实现 |
| `deepseek.base_url` | DeepSeek ASR 接口地址，默认 `https://api.deepseek.com/v1/tools/asr` |
| `tts.takes` | 每次合成生成几个候选，默认 3 |
| `volc.voice_type` | 指定音色 ID；留空则用参考音频克隆 |
| `render.speed_range` | 自动语速边界，默认 `[0.95, 1.10]` |
| `render.tolerance_abs` / `tolerance_rel` | 时长偏差容忍（取宽） |
| `render.crossfade_ms` / `noise_bed_gain` / `target_lufs` / `true_peak` | 拼接与响度参数 |
| `storage.data_dir` | 数据根目录，默认 `data` |
| `llm.enabled` | 是否启用 AI 决策（前端「AI 决策」开关可临时覆盖），默认 `false` |
| `llm.model` / `base_url` / `timeout_s` | DeepSeek 对话模型与超时；Key 复用 `DEEPSEEK_API_KEY` |
| `llm.speed_clamp` | AI 建议语速的硬边界，默认 `[0.95, 1.10]` |

> 修改 `config.yaml` 或 `.env` 后必须**重启 uvicorn** 才生效（provider 与配置在进程内缓存）。

## 使用流程

1. **上传**：拖拽或选择视频，后端抽 48kHz 双声道音轨。
2. **框口误区间**：在波形上拖出红区；切到「② 框参考音色」拖绿区选中该说话人的一段完整话（多人视频建议必选，不选则用口误区间做参考音）。点选区右上角 ✕ 可取消。
3. **改文本**：「识别此区间」→ 浏览器展示字级结果 → 编辑文本 → 选择自动语速或手动拖动滑块 →「生成候选音频」。
4. **择优渲染**：试听 3 个 take，点「选这个并渲染成片」，预览无误后下载 `patch_final.mp4`。

### 时长冲突怎么办

| 情况 | 表现 | 处理 |
| --- | --- | --- |
| A/B 类 | 偏差在容忍内或可被 5% 变速吸收 | 直接正常出片 |
| 轻微溢出 | `overflow = true` | 试听确认后仍可出片 |
| D 类 | `d_class = true`（新音频比窗口长 50% 以上） | 精简文本（口语化数字）、延长选区吃掉停顿，或整句重录/调整时间轴 |

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 健康检查 + provider 告警 |
| `POST` | `/api/projects` | 上传视频（multipart `file`、可选 `name`），抽轨建项目 |
| `GET` | `/api/projects` | 项目列表 |
| `GET` | `/api/projects/{pid}` | 项目详情 |
| `GET` | `/api/projects/{pid}/audio.wav` | 项目音轨 |
| `POST` | `/api/projects/{pid}/transcribe` | `{t0,t1}` 转写并返回字级时间戳 |
| `POST` | `/api/segments/{sid}/synthesize` | `{text,speed?,takes?,ref_t0?,ref_t1?,use_llm?,llm_speed_locked?}` 合成候选 |
| `POST` | `/api/segments/{sid}/render` | `{take_id}` 拼接、补底噪、响度归一、换轨导出 |

静态资源：`/ui/*` 前端页面，`/data/*` 项目视频/音频。

## 目录结构

```
voice-patch/
├─ run.py                 # uvicorn 启动入口（127.0.0.1:8765）
├─ config.yaml            # provider / 渲染 / 存储配置
├─ requirements.txt
├─ .env.example           # 密钥模板
├─ backend/               # 后端（详见 backend/README.md）
│  ├─ main.py             # FastAPI 路由与静态挂载
│  ├─ orchestrator.py     # 10 步流水线编排
│  ├─ config.py           # 配置加载（env > .env > yaml）
│  ├─ db.py               # SQLite：projects / segments / takes
│  ├─ pan_upload.py       # pan.ezv.cc 中转上传（仅 volc ASR 取公开 URL 时用）
│  ├─ providers/          # base 协议 + deepseek_asr / volc_asr / volc_tts / mock + registry
│  ├─ media/ffmpeg_ops.py # ffmpeg 封装
│  ├─ audio/fitting.py    # 自动语速、take 适配与择优
│  └─ text/normalize.py   # 数字/百分比/年份中文读法规整
├─ frontend/              # 前端源码（详见 frontend/README.md）
├─ web/                   # 前端构建产物（FastAPI 挂载到 /ui，已 gitignore）
├─ scripts/               # 冒烟 / 云服务 / HTTP 链路测试
└─ data/                  # 运行时数据（已 gitignore）
```

## 脚本与测试

```powershell
# 1) 全链路冒烟（mock provider，无需任何 Key；会造 6s 测试视频）
python scripts/smoke_test.py

# 2) 火山 TTS 鉴权/连通性
python scripts/test_volc_tts.py

# 3) TTS → 火山 ASR 真实闭环（需 VOLC_API_KEY + PAN_API_KEY）
python scripts/test_volc_asr.py

# 4) HTTP 链路（需先 python run.py）
python scripts/http_test.py
```

### 前端自动化测试（改前端代码后必跑）

```powershell
cd frontend
npm run check    # = tsc 类型检查 + vite build + 单测 + E2E，一键自检
npm run test     # 仅单元测试（Vitest + RTL，~4s）
npm run e2e      # 仅 E2E（Playwright 真 Chromium，~20s，自动拉起/复用后端）
```

- **单元测试**（[frontend/src/App.test.tsx](frontend/src/App.test.tsx)）：mock Audio/波形/网络，确定性复现竞态时序——试听起播位置、暂停续播、选区终点精确停、seek 期间旧位置 timeupdate 不误停、主播放互斥、重新上传全状态重置。
- **E2E**（[frontend/e2e/preview.spec.ts](frontend/e2e/preview.spec.ts)）：真实输入驱动完整链路——拖拽框选、试听自动停止、播完重点试听、暂停续播、重新上传重置、进度条游标与波形播放头对齐。
- E2E 首次运行需 `npx playwright install chromium` 下载浏览器；测试用视频取自 `test/350679646.mp4`。
- **约定：agent 修改前端代码后，必须跑 `npm run check` 全绿才算完成。**

## 说明与限制

- 一步只替换一个选区；多口误建议逐个渲染。
- 等长替换受窗口长度硬约束，大幅扩写（如“一千”→ 一长串数字）无法真正等长，会提示 D 类。
- 视频流 `-c:v copy`，导出速度取决于音频处理，不会二次损失画质。
- 默认 ASR 为 DeepSeek：单次上传 ≤120s、≤8MiB，只返回整段文本，**字级时间戳是按字符均分估算的**（点字为近似跳转）；需要真实时间戳时把 `asr.provider` 改回 `volc`。
- `volc` ASR 走 `pan.ezv.cc` 中转并等待审核，单次约 15~30 秒；转写完成后自动回收中转文件。
- `data/`、`.env` 已在 `.gitignore` 中，密钥与素材不会被提交。
