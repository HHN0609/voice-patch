# frontend/ · 前端

voice-patch 的 Web 界面：上传视频、在波形上框选口误区间与参考音色、编辑文本、试听候选 take、预览并下载成片。

技术栈：React 19 + TypeScript + Vite + wavesurfer.js 8（`@vitejs/plugin-react`，oxlint 做 lint）。

## 开发

```powershell
npm install
npm run dev      # http://127.0.0.1:5173
```

开发服务器把 `/api`、`/data` 代理到后端 `http://127.0.0.1:8765`（见 `vite.config.ts`），所以需要先启动后端：

```powershell
python run.py    # 在项目根目录
```

## 构建

```powershell
npm run build    # tsc -b && vite build
```

产物输出到项目根的 `web/` 目录（`base: '/ui/'`），由 FastAPI 挂载在 `/ui`。刷新 <http://127.0.0.1:8765/ui/> 即可看到。静态文件改动不需要重启后端。

> 不要直接编辑 `web/`，那是构建产物。

其他脚本：

- `npm run lint`：oxlint
- `npm run preview`：本地预览构建结果（不代理 API，仅看静态页面）

## 目录

```
frontend/
├─ index.html
├─ vite.config.ts        # base=/ui/、outDir=../web、dev 代理 /api 与 /data
├─ src/
│  ├─ main.tsx           # 入口
│  ├─ App.tsx            # 业务容器：上传 → 选区 → 转写 → 合成 → 试听 → 渲染
│  ├─ Waveform.tsx       # wavesurfer 封装：渲染波形 + 拖拽建区 + 区域着色/删除
│  ├─ api.ts             # 后端接口类型与 fetch 封装
│  └─ App.css            # 全部样式
└─ public/               # favicon 等静态资源
```

## 交互要点

- **两种选区**：红区（口误区间）与绿区（参考音色）。点模式按钮切换建区类型，点击已有区域即删除；「只读浏览」模式下不能新建。
- **播放同步**：视频元素 `muted`，声音统一由波形播放器输出，避免双轨重叠；播放/暂停按钮显式同时驱动 video 与 wavesurfer，播放前对齐两边时间。不要依赖 `requestAnimationFrame` 循环来暂停——`playing` 变 false 时循环会被 React 清理掉。
- **参考音色可选**：不框绿区时后端自动挑能量最高的一段；多人视频建议框选目标说话人，避免克隆到别人的声音。
- **语速**：默认「自动语速」（后端按窗口反推）；取消勾选后可用 0.90~1.10 滑块手动覆盖。
- **时长提示**：合成结果用颜色区分「匹配良好 / 轻微溢出 / D 类（文本过长）」，D 类建议精简文本。
- 音频请求走 `/data/projects/{id}/...`，由后端静态目录直接提供。

## 接口类型

`src/api.ts` 中的 `ProjectInfo`、`TranscribeResp`、`SynthResp`、`RenderResp` 与后端 `app/main.py` 的请求/响应结构一一对应；改后端字段时同步改这里，否则会静默拿到 `undefined`。
