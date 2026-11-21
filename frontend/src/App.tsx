import { useCallback, useEffect, useRef, useState } from 'react'
import type WaveSurfer from 'wavesurfer.js'
import { api, type ProjectInfo, type SynthResp, type TranscribeResp } from './api'
import Waveform, { type RegionMode, type RegionSel } from './Waveform'

const fmt = (t: number) => {
  const m = Math.floor(t / 60)
  const s = t - m * 60
  return `${m}:${s.toFixed(2).padStart(5, '0')}`
}

type Stage = 'upload' | 'work'

export default function App() {
  const [stage, setStage] = useState<Stage>('upload')
  const [project, setProject] = useState<ProjectInfo | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  // 波形 / 播放
  const [duration, setDuration] = useState(0)
  const [cursor, setCursor] = useState(0)
  const [playing, setPlaying] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  const wsRef = useRef<WaveSurfer | null>(null)
  // 最新光标位置（试听/拖动时 state 更新是异步的，播放前用它对齐）
  const cursorRef = useRef(0)

  // 选区
  const [mode, setMode] = useState<RegionMode | null>('mispeak')
  const [mispeak, setMispeak] = useState<RegionSel | null>(null)
  const [voice, setVoice] = useState<RegionSel | null>(null)

  // 选区试听（独立 Audio，不干扰波形/视频的播放状态）
  const previewRef = useRef<HTMLAudioElement | null>(null)
  const previewingRef = useRef(false)
  const previewRafRef = useRef(0)
  const [previewing, setPreviewing] = useState<RegionMode | ''>('')

  // 转写 / 合成 / 渲染
  const [seg, setSeg] = useState<TranscribeResp | null>(null)
  const [text, setText] = useState('')
  const [speed, setSpeed] = useState(1.0)
  const [speedAuto, setSpeedAuto] = useState(true)
  const [synth, setSynth] = useState<SynthResp | null>(null)
  const [selTake, setSelTake] = useState('')
  const [resultVideo, setResultVideo] = useState('')

  const onError = (e: unknown) => {
    setError(e instanceof Error ? e.message : String(e))
    setBusy('')
  }

  const onUpload = async (file: File) => {
    setError('')
    setBusy('上传并抽轨中…')
    try {
      const p = await api.uploadVideo(file, file.name.replace(/\.[^.]+$/, ''))
      setProject(p)
      setStage('work')
    } catch (e) {
      onError(e)
      return
    }
    setBusy('')
  }

  const onReady = useCallback((dur: number, ws: WaveSurfer) => {
    setDuration(dur)
    wsRef.current = ws
  }, [])

  // 试听时停止跟随：暂停试听音频与视频、取消进度跟随、复位按钮
  const stopPreview = useCallback(() => {
    previewRef.current?.pause()
    videoRef.current?.pause()
    previewingRef.current = false
    cancelAnimationFrame(previewRafRef.current)
    previewRafRef.current = 0
    setPreviewing('')
  }, [])

  // 波形时间更新：同步光标；暂停状态下顺带把画面帧对齐到同一位置
  const onCursor = useCallback((t: number) => {
    cursorRef.current = t
    setCursor(t)
    if (previewingRef.current) return // 试听只跟随进度条，不逐帧拖动画面
    const v = videoRef.current
    if (v && !wsRef.current?.isPlaying() && Math.abs(v.currentTime - t) > 0.1) {
      v.currentTime = t
    }
  }, [])

  // 拖动/点击时间轴跳转（音视频一起定位）
  const seekTo = (t: number) => {
    const ws = wsRef.current
    if (!ws) return
    if (previewingRef.current) stopPreview()
    const nt = Math.max(0, Math.min(t, duration || t))
    ws.setTime(nt)
    const v = videoRef.current
    if (v) v.currentTime = nt
    cursorRef.current = nt
    setCursor(nt)
  }

  // 试听选区：播完自动停；与主播放互斥
  const previewRange = (kind: RegionMode, sel: RegionSel | null) => {
    if (!sel || !project) return
    if (previewing === kind) {
      stopPreview()
      return
    }
    const ws = wsRef.current
    if (ws?.isPlaying()) {
      ws.pause()
      setPlaying(false)
    }
    videoRef.current?.pause()
    previewingRef.current = true

    let a = previewRef.current
    if (!a) {
      a = new Audio()
      a.addEventListener('ended', () => stopPreview())
      previewRef.current = a
    }
    if (!a.src.endsWith(project.audio_url)) {
      a.src = project.audio_url
      a.currentTime = sel.t0
    }
    // 起播时刻只用已知量计算，不依赖赋值后回读 currentTime（seek 异步，回读可能拿到旧值）
    // 暂停后继续：若仍停在选区内则从当前位置续播，否则回到选区开头
    const resume = a.currentTime > sel.t0 + 0.01 && a.currentTime < sel.t1 - 0.01
    const startAt = resume ? a.currentTime : sel.t0
    if (!resume) a.currentTime = startAt
    ws?.setTime(startAt) // 光标/画面定位到播放起点
    a.ontimeupdate = () => {
      // 兜底；seek 未生效时 currentTime 是旧值，靠 seeking 标志防误停
      if (a && !a.seeking && a.currentTime >= sel.t1) stopPreview()
    }
    // 等 seek 生效再播：避免 seek 完成前从旧位置出声、或旧位置的 timeupdate 误触停止
    // rAF 跟随循环必须等真正起播后再启动——提前启动会因 paused 在第一帧退出
    const tick = () => {
      const el = previewRef.current
      if (!el || el.paused) {
        previewRafRef.current = 0
        return
      }
      if (el.currentTime >= sel.t1) {
        stopPreview()
        return
      }
      // 持续纠正视频相位差，保证口型与试听声音同步
      const v = videoRef.current
      if (v && !v.paused && Math.abs(v.currentTime - el.currentTime) > 0.15) {
        v.currentTime = el.currentTime
      }
      setCursor(el.currentTime)
      cursorRef.current = el.currentTime
      wsRef.current?.setTime(el.currentTime)
      previewRafRef.current = requestAnimationFrame(tick)
    }
    const doPlay = () => {
      // 视频画面与试听音频同步真实播放（视频静音，声音来自试听 Audio）。
      // 二者 seek 延迟不同（实测音频可慢 200ms+），若同时 play() 视频会先跑、
      // 停止时光标被超前的视频位置拽过选区末尾——所以等音频真正出声
      // （playing 事件）后再对齐音频当前位置起播视频。
      const v = videoRef.current
      if (v) {
        a!.addEventListener('playing', () => {
          if (!previewingRef.current) return
          v.currentTime = a!.currentTime
          v.play().catch(() => { /* 视频起播失败不影响音频试听 */ })
        }, { once: true })
      }
      a!.play().catch(() => stopPreview())
      cancelAnimationFrame(previewRafRef.current)
      previewRafRef.current = requestAnimationFrame(tick)
    }
    if (a.seeking) {
      a.addEventListener('seeked', doPlay, { once: true })
    } else {
      doPlay()
    }
    setPreviewing(kind)
  }

  // 卸载时停掉试听与跟随
  useEffect(() => () => {
    previewRef.current?.pause()
    cancelAnimationFrame(previewRafRef.current)
  }, [])

  // 波形 → 视频 同步
  useEffect(() => {
    if (!playing) return
    let raf = 0
    const tick = () => {
      const ws = wsRef.current
      const v = videoRef.current
      if (ws && v) {
        const t = ws.getCurrentTime()
        if (Math.abs(v.currentTime - t) > 0.18 && !v.seeking) v.currentTime = t
        if (ws.isPlaying() && v.paused) void v.play()
        if (!ws.isPlaying() && !v.paused) v.pause()
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing])

  const togglePlay = () => {
    const ws = wsRef.current
    const v = videoRef.current
    if (!ws) return
    if (ws.isPlaying()) {
      ws.pause()
      v?.pause() // 显式暂停视频，不依赖同步循环（循环会被 playing=false 取消）
    } else {
      stopPreview()
      // 试听后播放器的内部位置可能落后于光标，先强制对齐再播放
      const t = cursorRef.current
      if (Math.abs(ws.getCurrentTime() - t) > 0.01) ws.setTime(t)
      if (v) v.currentTime = t
      void ws.play()
    }
    setPlaying(ws.isPlaying())
  }

  const doTranscribe = async () => {
    if (!project || !mispeak) return
    setError('')
    setBusy('识别中（上传中转 + 云 ASR，约 15~30 秒）…')
    try {
      const r = await api.transcribe(project.id, mispeak.t0, mispeak.t1)
      setSeg(r)
      setText(r.text_raw)
      setSynth(null)
      setResultVideo('')
    } catch (e) {
      onError(e)
      return
    }
    setBusy('')
  }

  const doSynthesize = async () => {
    if (!seg || !text.trim()) return
    setError('')
    setBusy('克隆音色并合成候选（3 次 TTS，约 1~2 分钟）…')
    try {
      const r = await api.synthesize(
        seg.segment_id, text,
        speedAuto ? null : speed,
        voice,
      )
      setSynth(r)
      if (r.speed) setSpeed(r.speed)
    } catch (e) {
      onError(e)
      return
    }
    setBusy('')
  }

  const doRender = async (takeId: string) => {
    if (!seg) return
    setError('')
    setBusy('拼接渲染中…')
    setSelTake(takeId)
    try {
      const r = await api.render(seg.segment_id, takeId)
      setResultVideo(r.video)
    } catch (e) {
      onError(e)
      return
    }
    setBusy('')
  }

  // 弃当前项目，回到上传页（传错视频时重新选择）
  const resetToUpload = () => {
    stopPreview()
    // 试听 Audio 仍持有旧项目音频，彻底释放，下次试听重建
    if (previewRef.current) {
      previewRef.current.src = ''
      previewRef.current = null
    }
    wsRef.current?.pause()
    videoRef.current?.pause()
    setPlaying(false)
    setMode('mispeak')
    setProject(null)
    setMispeak(null)
    setVoice(null)
    setSeg(null)
    setText('')
    setSpeed(1.0)
    setSpeedAuto(true)
    setSynth(null)
    setSelTake('')
    setResultVideo('')
    setCursor(0)
    cursorRef.current = 0
    setDuration(0)
    setError('')
    setBusy('')
    setStage('upload')
  }

  if (stage === 'upload' || !project) {
    return <Upload onUpload={onUpload} busy={busy} error={error} />
  }

  return (
    <div className="app">
      <header>
        <h1>voice-patch · 口误修复</h1>
        <div className="proj-name">{project.name}</div>
        <button className="btn ghost reupload" onClick={resetToUpload}>重新上传</button>
      </header>

      {/* 1. 视频画面 + 波形 */}
      <section className="card">
        <div className="video-row">
          <video
            ref={videoRef}
            src={project.video_url}
            className="video"
            muted // 声音统一由波形播放器出，视频轨静音避免双声
            onLoadedMetadata={(e) => { e.currentTarget.muted = true }}
            onTimeUpdate={(e) => {
              // 视频暂停拖动时反向同步光标（拖进度条场景）；试听期间由试听 tick 主导，不反向同步
              if (previewingRef.current) return
              const v = e.currentTarget
              if (!wsRef.current?.isPlaying()) wsRef.current?.setTime(v.currentTime)
            }}
          />
          <div className="video-side">
            <button className="btn primary" onClick={togglePlay}>
              {playing ? '⏸ 暂停' : '▶ 播放'}
            </button>
            <div className="time">{fmt(cursor)} / {fmt(duration)}</div>
            <div className="hint">
              在波形上<b style={{ color: '#ff7676' }}>拖拽红区</b>框选口误区间；
              切到<b style={{ color: '#5fd39a' }}>参考音色</b>后拖拽绿区，选中该说话人一段完整的话（多人视频必选）。
              点击选区右上角的 <b>✕</b> 可取消该选区；<b>拖动下方进度条</b>调播放进度，<b>单击波形</b>也能跳到对应位置。
            </div>
          </div>
        </div>
        <div className="mode-bar">
          <button
            className={`btn mode mispeak ${mode === 'mispeak' ? 'on' : ''}`}
            onClick={() => setMode('mispeak')}
          >① 框口误区间</button>
          <button
            className={`btn mode voice ${mode === 'voice' ? 'on' : ''}`}
            onClick={() => setMode('voice')}
          >② 框参考音色{voice ? ' ✓' : ''}</button>
          <button
            className={`btn ghost ${mode === null ? 'on' : ''}`}
            onClick={() => setMode(null)}
          >只读浏览</button>
        </div>
        <Waveform
          audioUrl={project.audio_url}
          mode={mode}
          mispeak={mispeak}
          voice={voice}
          onChange={(kind, sel) => {
            if (kind === 'mispeak') setMispeak(sel)
            else setVoice(sel)
          }}
          onReady={onReady}
          onTime={onCursor}
        />
        <div className="timeline">
          <input
            className="scrubber"
            type="range"
            min={0}
            max={duration || 0.01}
            step={0.01}
            value={Math.min(cursor, duration || 0)}
            disabled={!duration}
            onChange={(e) => seekTo(Number(e.target.value))}
            aria-label="播放进度"
          />
          <div className="tl-labels">
            <span className="mono">{fmt(cursor)}</span>
            <span className="mono">{fmt(duration)}</span>
          </div>
        </div>
        <div className="sel-info">
          <span>
            口误区间：{mispeak
              ? `${fmt(mispeak.t0)} → ${fmt(mispeak.t1)}（时长 ${(mispeak.t1 - mispeak.t0).toFixed(2)}s）`
              : '未选'}
            {mispeak && (
              <button className="btn mini" onClick={() => previewRange('mispeak', mispeak)}>
                {previewing === 'mispeak' ? '■ 停止' : '▶ 试听'}
              </button>
            )}
          </span>
          <span>
            参考音色：{voice
              ? `${fmt(voice.t0)} → ${fmt(voice.t1)}（时长 ${(voice.t1 - voice.t0).toFixed(2)}s）`
              : '未选（默认用口误区间）'}
            {voice && (
              <button className="btn mini" onClick={() => previewRange('voice', voice)}>
                {previewing === 'voice' ? '■ 停止' : '▶ 试听'}
              </button>
            )}
          </span>
          <button
            className="btn primary"
            disabled={!mispeak || !!busy}
            onClick={doTranscribe}
          >识别此区间</button>
        </div>
      </section>

      {seg && (
        <section className="card">
          <h2>② 修改文本</h2>
          <div className="words">
            {/* 词时间是 ASR 裁切片段内的相对时间，跳转要加上窗口起点 T0 */}
            {seg.words.map((w, i) => (
              <span key={i} className="word" onClick={() => wsRef.current?.setTime(seg.T0 + w.start)}>
                {w.text}
              </span>
            ))}
          </div>
          <textarea
            className="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={2}
          />
          <div className="speed-row">
            <label>
              <input
                type="checkbox"
                checked={speedAuto}
                onChange={(e) => setSpeedAuto(e.target.checked)}
              />
              自动语速
            </label>
            <input
              type="range" min={0.9} max={1.1} step={0.01}
              value={speed}
              disabled={speedAuto}
              onChange={(e) => setSpeed(Number(e.target.value))}
            />
            <span className="mono">{speed.toFixed(2)}×</span>
            <span className="hint">语音 {seg.speech.toFixed(2)}s · 窗口 {seg.window.toFixed(2)}s</span>
            <button className="btn primary" disabled={!!busy} onClick={doSynthesize}>
              生成候选音频
            </button>
          </div>
        </section>
      )}

      {synth && (
        <section className="card">
          <h2>③ 试听并选择 take</h2>
          <div className={`warn ${synth.overflow ? 'bad' : 'ok'}`}>
            {synth.d_class
              ? '⚠ 新文本比窗口长太多（D 类），等长替换质量有限，建议精简文本（如口语化数字）'
              : synth.overflow
                ? '⚠ 时长有轻微溢出，take 已做适配，请试听确认'
                : `✓ 时长匹配良好（偏差 ${synth.delta.toFixed(2)}s）· 参考音来源：${synth.voice_source === 'user' ? '你选中的区间' : '口误区间'}`}
          </div>
          <div className="takes">
            {synth.takes.map((t) => (
              <div key={t.take_id} className={`take ${selTake === t.take_id ? 'sel' : ''}`}>
                <div className="take-head">
                  <b>Take {t.take_no}</b>
                  <span className="mono">{t.final_dur.toFixed(2)}s · Δ{t.delta.toFixed(2)}s</span>
                  <span className={`badge ${t.status}`}>{t.status}</span>
                </div>
                <audio controls src={t.wav} preload="none" />
                <button
                  className="btn primary"
                  disabled={!!busy}
                  onClick={() => doRender(t.take_id)}
                >选这个并渲染成片</button>
              </div>
            ))}
          </div>
        </section>
      )}

      {resultVideo && (
        <section className="card">
          <h2>④ 成片预览</h2>
          <video src={resultVideo} controls className="video wide" />
          <a className="btn" href={resultVideo} download>下载成片</a>
        </section>
      )}

      {busy && <div className="busy">{busy}</div>}
      {error && <div className="err" onClick={() => setError('')}>{error}（点击关闭）</div>}
    </div>
  )
}

function Upload({ onUpload, busy, error }: {
  onUpload(f: File): void
  busy: string
  error: string
}) {
  const [drag, setDrag] = useState(false)
  return (
    <div className="app center">
      <h1>voice-patch · 口误修复</h1>
      <p className="hint">上传含口误的视频，框选区间 → 改字 → 克隆音色重说 → 无缝拼回视频。</p>
      <label
        className={`drop ${drag ? 'over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDrag(false)
          const f = e.dataTransfer.files[0]
          if (f) onUpload(f)
        }}
      >
        <input
          type="file"
          accept="video/*"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) onUpload(f)
          }}
        />
        {busy || '点击选择 或 拖拽视频到此处'}
      </label>
      {error && <div className="err">{error}</div>}
    </div>
  )
}
