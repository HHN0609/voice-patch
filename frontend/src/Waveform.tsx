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

    // 拖拽松手新建的区域在此打模式标记并着色
    regions.on('region-created', (r: Region) => {
      const tag = r as Region & { _kind?: RegionMode }
      if (!tag._kind) {
        if (!modeRef.current) {
          r.remove()
          return
        }
        tag._kind = modeRef.current
        r.setOptions({ color: COLORS[modeRef.current], id: `r-${modeRef.current}` })
      }
    })
    regions.on('region-updated', (r: Region) => {
      const kind = (r as Region & { _kind?: RegionMode })._kind
      if (kind) cb.current.onChange(kind, { t0: r.start, t1: r.end })
    })
    regions.on('region-clicked', (r: Region, e: MouseEvent) => {
      e.stopPropagation()
      const kind = (r as Region & { _kind?: RegionMode })._kind
      if (kind) cb.current.onChange(kind, null) // 点击区域 = 删除该选区
    })

    ws.on('ready', () => {
      readyRef.current = true
      cb.current.onReady(ws.getDuration(), ws)
    })
    ws.on('timeupdate', (t: number) => cb.current.onTime(t))
    regions.enableDragSelection({ color: 'rgba(0,0,0,0.01)' })

    return () => {
      ws.destroy()
      regionsRef.current = null
      readyRef.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.audioUrl])

  // 外部状态（模式切换/清除）变化时按最新选区重建区域
  useEffect(() => {
    if (!readyRef.current || !regionsRef.current) return
    const regions = regionsRef.current
    regions.clearRegions()
    const add = (kind: RegionMode, sel: RegionSel | null) => {
      if (!sel) return
      regions.addRegion({
        id: `r-${kind}`,
        start: sel.t0,
        end: sel.t1,
        color: COLORS[kind],
        drag: true,
        resize: true,
        _kind: kind,
      } as never)
    }
    add('mispeak', props.mispeak)
    add('voice', props.voice)
  }, [props.mispeak, props.voice, props.mode])

  return <div className="waveform-holder" ref={holder} />
}
