// 后端 API 类型与请求封装
export interface ProjectInfo {
  id: string
  name: string
  duration: number
  video_url: string
  audio_url: string
}

export interface Word {
  text: string
  start: number
  end: number
  confidence: number
}

export interface TranscribeResp {
  segment_id: string
  t0: number
  t1: number
  T0: number
  T1: number
  window: number
  /** 窗口内实际语音跨度（静音垫除外），时长适配的目标 */
  speech: number
  words: Word[]
  text_raw: string
  text_norm: string
}

export interface Take {
  take_id: string
  take_no: number
  final_dur: number
  delta: number
  stretch: number
  status: string
  speed: number
  seed: number
  wav: string
}

export interface SynthResp {
  segment_id: string
  text_norm: string
  speed: number
  window: number
  takes: Take[]
  voice_source: 'user' | 'mispeak'
  overflow: boolean
  delta: number
  d_class: boolean
}

export interface RenderResp {
  video: string
  original_audio: string
  final_dur: number
  delta: number
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  if (!r.ok) {
    let msg = `HTTP ${r.status}`
    try {
      const body = await r.json()
      msg = body.detail || msg
    } catch { /* ignore */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}

export const api = {
  uploadVideo(file: File, name: string) {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('name', name)
    return req<ProjectInfo>('/api/projects', { method: 'POST', body: fd })
  },
  project(pid: string) {
    return req<ProjectInfo>(`/api/projects/${pid}`)
  },
  transcribe(pid: string, t0: number, t1: number) {
    return req<TranscribeResp>(`/api/projects/${pid}/transcribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ t0, t1 }),
    })
  },
  synthesize(
    sid: string,
    text: string,
    speed: number | null,
    ref: { t0: number; t1: number } | null,
  ) {
    return req<SynthResp>(`/api/segments/${sid}/synthesize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        speed,
        ref_t0: ref?.t0 ?? null,
        ref_t1: ref?.t1 ?? null,
      }),
    })
  },
  render(sid: string, takeId: string) {
    return req<RenderResp>(`/api/segments/${sid}/render`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ take_id: takeId }),
    })
  },
}
