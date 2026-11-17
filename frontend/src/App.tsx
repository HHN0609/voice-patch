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

  // 选区
  const [mode, setMode] = useState<RegionMode | null>('mispeak')
  const [mispeak, setMispeak] = useState<RegionSel | null>(null)
  const [voice, setVoice] = useState<RegionSel | null>(null)

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

  // 波形时间更新：同步光标；暂停状态下顺带把画面帧对齐到同一位置
  const onCursor = useCallback((t: number) => {
    setCursor(t)
    const v = videoRef.current
    if (v && !wsRef.current?.isPlaying() && Math.abs(v.currentTime - t) > 0.1) {
      v.currentTime = t
    }
  }, [])

  // 拖动/点击时间轴跳转（音视频一起定位）
  const seekTo = (t: number) => {
    const ws = wsRef.current
    if (!ws) return
    const nt = Math.max(0, Math.min(t, duration || t))
    ws.setTime(nt)
    const v = videoRef.current
    if (v) v.currentTime = nt
    setCursor(nt)
  }

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
      if (v && Math.abs(v.currentTime - ws.getCurrentTime()) > 0.18) {
        v.currentTime = ws.getCurrentTime()
      }
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

  if (stage === 'upload' || !project) {
    return <Upload onUpload={onUpload} busy={busy} error={error} />
  }

  return (
    <div className="app">
      <header>
        <h1>voice-patch · 口误修复</h1>
        <div className="proj-name">{project.name}</div>
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
              // 视频暂停拖动时反向同步光标（拖进度条场景）
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
          <span>口误区间：{mispeak ? `${fmt(mispeak.t0)} → ${fmt(mispeak.t1)}` : '未选'}</span>
          <span>参考音色：{voice ? `${fmt(voice.t0)} → ${fmt(voice.t1)}` : '未选（自动挑选）'}</span>
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
            {seg.words.map((w, i) => (
              <span key={i} className="word" onClick={() => wsRef.current?.setTime(w.start)}>
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
            <span className="hint">窗口 {seg.window.toFixed(2)}s</span>
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
                : `✓ 时长匹配良好（偏差 ${synth.delta.toFixed(2)}s）· 参考音来源：${synth.voice_source === 'user' ? '你选中的区间' : '自动挑选'}`}
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
