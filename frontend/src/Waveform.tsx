import { useEffect, useRef } from 'react'
import WaveSurfer from 'wavesurfer.js'
import RegionsPlugin, { type Region } from 'wavesurfer.js/dist/plugins/regions.esm.js'

type Regions = InstanceType<typeof RegionsPlugin>

export type RegionMode = 'mispeak' | 'voice'

export interface RegionSel {
  t0: number
  t1: number
}

interface Props {
  audioUrl: string
  /** 当前建区模式；null = 只读（不可拖拽建区） */
  mode: RegionMode | null
  mispeak: RegionSel | null
  voice: RegionSel | null
  onChange(kind: RegionMode, sel: RegionSel | null): void
  onReady(duration: number, ws: WaveSurfer): void
  onTime(t: number): void
}

const COLORS: Record<RegionMode, string> = {
  mispeak: 'rgba(255,86,86,0.25)',
  voice: 'rgba(48,200,120,0.20)',
}

/** 波形组件：渲染 + 选区交互；播放同步由父组件拿 ws 实例驱动 */
export default function Waveform(props: Props) {
  const holder = useRef<HTMLDivElement>(null)
  const modeRef = useRef<RegionMode | null>(props.mode)
  const cb = useRef(props)
  const readyRef = useRef(false)
  const regionsRef = useRef<Regions | null>(null)
  const dragOffRef = useRef<null | (() => void)>(null)
  const internalRef = useRef(false)
  cb.current = props
  modeRef.current = props.mode

  useEffect(() => {
    if (!holder.current) return
    const ws = WaveSurfer.create({
      container: holder.current,
      waveColor: '#3f4756',
      progressColor: '#4f8cff',
      cursorColor: '#e8ecf3',
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      height: 96,
      normalize: true,
      url: props.audioUrl,
    })
    const regions = ws.registerPlugin(RegionsPlugin.create())
    regionsRef.current = regions

    // 拖拽松手新建的区域在此打模式标记、着色，并把选区回传给父组件
    // 注意：v8 的拖拽建区只发 region-created（不发 region-updated），
    // 这里不回传的话选区只存在于画面上，切换模式时会被父组件的空状态清掉
    regions.on('region-created', (r: Region) => {
      if (internalRef.current) return // 由下方 effect 重建的区域，状态本身已知
      const tag = r as Region & { _kind?: RegionMode }
      if (tag._kind) return
      if (!modeRef.current) {
        r.remove()
        return
      }
      tag._kind = modeRef.current
      r.setOptions({ color: COLORS[modeRef.current] })
      cb.current.onChange(modeRef.current, { t0: r.start, t1: r.end })
    })
    regions.on('region-updated', (r: Region) => {
      const kind = (r as Region & { _kind?: RegionMode })._kind
      if (kind) cb.current.onChange(kind, { t0: r.start, t1: r.end })
    })

    ws.on('ready', () => {
      readyRef.current = true
      cb.current.onReady(ws.getDuration(), ws)
    })
    ws.on('timeupdate', (t: number) => cb.current.onTime(t))
    // 只有框选模式下才允许在波形上拖拽建区；只读模式下不建区（点击仍可跳转）
    if (modeRef.current) {
      dragOffRef.current = regions.enableDragSelection({ color: 'rgba(0,0,0,0.01)' })
    }

    return () => {
      dragOffRef.current?.()
      dragOffRef.current = null
      ws.destroy()
      regionsRef.current = null
      readyRef.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.audioUrl])

  // 模式切换时开关“拖拽建区”，避免只读浏览时误建选区
  useEffect(() => {
    const regions = regionsRef.current
    if (!regions) return
    if (props.mode && !dragOffRef.current) {
      dragOffRef.current = regions.enableDragSelection({ color: 'rgba(0,0,0,0.01)' })
    } else if (!props.mode && dragOffRef.current) {
      dragOffRef.current()
      dragOffRef.current = null
    }
  }, [props.mode])

  // 外部状态（模式切换/清除）变化时按最新选区重建区域
  useEffect(() => {
    if (!readyRef.current || !regionsRef.current) return
    const regions = regionsRef.current
    const add = (kind: RegionMode, sel: RegionSel | null) => {
      if (!sel) return
      // 右上角清除按钮：只取消对应选区，不触发跳转/拖拽
      // 区域渲染在 WaveSurfer 的 shadow DOM 内，页面 CSS 不生效，样式必须内联
      const clear = document.createElement('div')
      Object.assign(clear.style, {
        position: 'absolute', top: '2px', right: '2px', padding: '0', zIndex: '3',
      })
      const btn = document.createElement('button')
      btn.type = 'button'
      btn.title = '取消该选区'
      btn.textContent = '✕'
      Object.assign(btn.style, {
        width: '16px', height: '16px', padding: '0', display: 'flex',
        alignItems: 'center', justifyContent: 'center', fontSize: '10px',
        lineHeight: '1', color: '#fff', cursor: 'pointer',
        background: 'rgba(0,0,0,0.55)', border: '1px solid rgba(255,255,255,0.45)',
        borderRadius: '50%',
      })
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation()
        cb.current.onChange(kind, null)
      })
      clear.appendChild(btn)

      internalRef.current = true
      try {
        const r = regions.addRegion({
          id: `r-${kind}`,
          start: sel.t0,
          end: sel.t1,
          color: COLORS[kind],
          drag: true,
          resize: true,
          content: clear,
          avoidOverlapping: false,
        })
        ;(r as Region & { _kind?: RegionMode })._kind = kind
      } finally {
        internalRef.current = false
      }
    }
    // 清掉旧区域后按最新状态重建（重建期间屏蔽 region-created 的副作用）
    regions.clearRegions()
    add('mispeak', props.mispeak)
    add('voice', props.voice)
  }, [props.mispeak, props.voice, props.mode])

  return <div className="waveform-holder" ref={holder} />
}
