import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { api, type SynthResp } from './api'
import { FakeAudio } from './test/setup'

// mock 网络层：上传 / 转写 / 合成 / 渲染全部走假数据，测试不触网
const M = vi.hoisted(() => {
  const project = {
    id: 'p1', name: 'test.mp4', duration: 10, has_video: true,
    video_url: '/data/p1/source.mp4', audio_url: '/data/p1/audio.wav',
  }
  const plan = {
    source: 'rules',
    reason: '',
    text_simplified: null,
    speed: { rules: 1.10, llm_raw: null, applied: 1.10, clamped: false, source: 'rules' },
    pad_side: { rules: 'tail', llm_raw: null, applied: 'tail', source: 'rules' },
    speech: { target: 1.77, body: 1.93, delta: 0.16 },
    silence: { head_before: 0.16, head_after: 0.09, tail_before: 0.09, tail_after: 0 },
  }
  return {
    project,
    transcribe: {
      segment_id: 's1', t0: 1, t1: 3, T0: 1, T1: 3, window: 2.0, speech: 1.77,
      words: [], text_raw: '国产新型舰载机', text_norm: '国产新型舰载机',
    },
    plan,
    synth: {
      segment_id: 's1', text_norm: '国产新型舰载机', speed: 1.10,
      window: 2.0, speech: 1.77,
      takes: [{
        take_id: 't1', take_no: 1, final_dur: 2.0, delta: 0, stretch: 1,
        status: 'ok', speed: 1.1, seed: 0, wav: '/t1.wav',
      }],
      voice_source: 'mispeak', overflow: false, delta: 0, d_class: false, plan,
    },
  }
})

vi.mock('./api', () => ({
  api: {
    uploadVideo: vi.fn(async () => M.project),
    transcribe: vi.fn(async () => M.transcribe),
    synthesize: vi.fn(async () => M.synth),
    render: vi.fn(async () => ({
      video: '/v.mp4', original_audio: '', final_dur: 2, delta: 0,
    })),
  },
}))

// mock 波形组件：暴露按钮模拟"波形就绪"与"框选口误区间"
const fakeWs = {
  playing: false,
  isPlaying() { return this.playing },
  pause: vi.fn(() => { fakeWs.playing = false }),
  play: vi.fn(() => { fakeWs.playing = true }),
  setTime: vi.fn(),
  getCurrentTime: vi.fn(() => 0),
}
vi.mock('./Waveform', () => ({
  default: (props: {
    onReady(d: number, ws: unknown): void
    onChange(kind: 'mispeak' | 'voice', sel: { t0: number; t1: number } | null): void
  }) => (
    <div>
      <button data-testid="ws-ready" onClick={() => props.onReady(10, fakeWs)}>ready</button>
      <button data-testid="mk-region" onClick={() => props.onChange('mispeak', { t0: 1, t1: 3 })}>region</button>
      <button data-testid="mk-region2" onClick={() => props.onChange('mispeak', { t0: 5, t1: 6 })}>region2</button>
    </div>
  ),
}))

const flushRaf = (window as unknown as { __flushRaf: (t?: number) => number }).__flushRaf

async function enterWorkStage() {
  render(<App />)
  const input = document.querySelector('input[type=file]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [new File(['x'], 't.mp4', { type: 'video/mp4' })] } })
  await screen.findByText('① 框口误区间')
  fireEvent.click(screen.getByTestId('ws-ready'))
  fireEvent.click(screen.getByTestId('mk-region'))
}

const previewBtn = () => screen.getAllByText(/试听|停止/).at(-1)!

beforeEach(() => { FakeAudio.instances = [] })
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('选区试听状态机', () => {
  it('起播从选区开头 t0 开始', async () => {
    await enterWorkStage()
    fireEvent.click(previewBtn())
    const a = FakeAudio.instances.at(-1)!
    expect(a.currentTime).toBe(1)
    expect(a.paused).toBe(false)
  })

  it('播完自动停后重点试听：回到 t0 且不会误触停止（bug1 竞态）', async () => {
    await enterWorkStage()
    fireEvent.click(previewBtn())
    const a = FakeAudio.instances.at(-1)!
    // 播到选区外（timeupdate 粒度 overshoot），rAF 判停
    a.currentTime = 3.02
    a.dispatch('timeupdate')
    flushRaf()
    expect(a.paused).toBe(true)
    await waitFor(() => expect(previewBtn().textContent).toContain('试听')) // setPreviewing 异步生效
    // 再点：currentTime≈t1（旧位置），必须回到 t0；seek 期间的旧值 timeupdate 不得误停
    a.asyncSeek = true
    fireEvent.click(previewBtn())
    expect(a.currentTime).toBe(3.02) // seek 未生效，getter 仍是旧值（worst case）
    expect(a.paused).toBe(true)     // 等 seeked 才播
    a.dispatch('timeupdate')        // 旧位置 ≥ t1，seeking=true 防误停
    expect(a.paused).toBe(true)
    expect(a.playCount).toBe(1)     // seek 未生效前不得重复 play（只有第一次试听那次）
    expect(flushRaf()).toBe(0)      // 起播前 rAF 跟随循环不应存活（paused 会误判停止）
    a.finishSeek()
    expect(a.currentTime).toBe(1)
    expect(a.paused).toBe(false)
    expect(a.playCount).toBe(2)     // seeked 后才起播
    expect(flushRaf()).toBe(1)      // 起播后跟随循环存活，继续调度下一帧
  })

  it('暂停续播：从暂停位置继续，而不是选区开头', async () => {
    await enterWorkStage()
    fireEvent.click(previewBtn())
    const a = FakeAudio.instances.at(-1)!
    a.currentTime = 2
    fireEvent.click(previewBtn()) // 停止
    expect(a.paused).toBe(true)
    fireEvent.click(previewBtn()) // 续播
    expect(a.currentTime).toBe(2)
    expect(a.paused).toBe(false)
  })

  it('rAF 精确停止：t1 之前不停，到达 t1 立即停（bug2 早停/晚停）', async () => {
    await enterWorkStage()
    fireEvent.click(previewBtn())
    const a = FakeAudio.instances.at(-1)!
    a.currentTime = 2.99
    flushRaf()
    expect(a.paused).toBe(false)
    a.currentTime = 3.0
    flushRaf()
    expect(a.paused).toBe(true)
  })

  it('换选区后旧播放位置不参与续播判定', async () => {
    await enterWorkStage()
    fireEvent.click(previewBtn())
    const a = FakeAudio.instances.at(-1)!
    a.currentTime = 2.5
    fireEvent.click(previewBtn()) // 停在 2.5（属于旧选区 1→3）
    fireEvent.click(screen.getByTestId('mk-region2')) // 新选区 5→6
    fireEvent.click(previewBtn())
    expect(a.currentTime).toBe(5) // 2.5 在新选区外，回到新 t0
  })

  it('与主播放互斥，试听结束不恢复主播放', async () => {
    await enterWorkStage()
    fakeWs.playing = true // 模拟主播放中
    fireEvent.click(previewBtn())
    expect(fakeWs.pause).toHaveBeenCalled()
    expect(fakeWs.playing).toBe(false)
    const a = FakeAudio.instances.at(-1)!
    a.currentTime = 3.02
    a.dispatch('timeupdate')
    flushRaf()
    expect(a.paused).toBe(true)
    expect(fakeWs.playing).toBe(false) // 试听结束不恢复主播放
  })
})

describe('重新上传状态重置', () => {
  it('回到上传页，试听 Audio 释放', async () => {
    await enterWorkStage()
    fireEvent.click(previewBtn())
    const a = FakeAudio.instances.at(-1)!
    fireEvent.click(screen.getByText('重新上传'))
    expect(screen.getByText(/点击选择/)).toBeTruthy()
    expect(a.src).toBe('')
    expect(a.paused).toBe(true)
  })

  it('重传后模式、选区、语速等全部回到默认', async () => {
    await enterWorkStage()
    fireEvent.click(screen.getByText('② 框参考音色')) // 切走默认模式
    fireEvent.click(screen.getByText('重新上传'))
    const input = document.querySelector('input[type=file]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['x'], 't2.mp4', { type: 'video/mp4' })] } })
    await waitFor(() => expect(screen.getByText('① 框口误区间').className).toContain('on'))
    // 模式按钮只有「① 框口误区间」高亮，选区未选，可再次重新上传
    expect(screen.getByText('② 框参考音色').className).not.toContain('on')
    expect(screen.getByText(/口误区间：未选/)).toBeTruthy()
    expect(screen.getByText(/参考音色：未选/)).toBeTruthy()
    expect(screen.getByText('重新上传')).toBeTruthy()
  })
})

// ---------------------------------------------------------------- 语速三态
const segBtn = (name: string) => screen.getByRole('button', { name })
const speedRange = () => document.querySelector('.speed-line input[type=range]')

/** 走到「② 修改文本」：上传 → 就绪 → 框选 → 识别 */
async function enterTextStage() {
  await enterWorkStage()
  fireEvent.click(screen.getByText('识别此区间'))
  await screen.findByText('② 修改文本')
}

describe('语速三态', () => {
  it('默认是「自动」，未开 AI 时不出现「AI 决定」', async () => {
    await enterTextStage()
    expect(segBtn('自动').className).toContain('on')
    expect(segBtn('手动').className).not.toContain('on')
    expect(screen.queryByRole('button', { name: 'AI 决定' })).toBeNull()
    // 自动态只给只读结论，不出滑块
    expect(speedRange()).toBeNull()
  })

  it('打开 AI 决策后语速默认切到「AI 决定」', async () => {
    await enterTextStage()
    fireEvent.click(screen.getByLabelText('AI 决策'))
    expect(segBtn('AI 决定').className).toContain('on')
    expect(segBtn('自动').className).not.toContain('on')
  })

  it('关闭 AI 决策后「AI 决定」回落到「自动」', async () => {
    await enterTextStage()
    const sw = screen.getByLabelText('AI 决策')
    fireEvent.click(sw) // 开
    fireEvent.click(sw) // 关
    expect(screen.queryByRole('button', { name: 'AI 决定' })).toBeNull()
    expect(segBtn('自动').className).toContain('on')
  })

  it('手动态出现滑块，切回自动后隐藏', async () => {
    await enterTextStage()
    fireEvent.click(segBtn('手动'))
    expect(speedRange()).toBeTruthy()
    fireEvent.click(segBtn('自动'))
    expect(speedRange()).toBeNull()
  })

  it('自动态先显示待计算，生成后给出语速与来源', async () => {
    await enterTextStage()
    expect(screen.getByText('待计算')).toBeTruthy()
    fireEvent.click(screen.getByText('生成候选音频'))
    await screen.findByText('编排账本')
    expect(screen.getByText(/1\.00 → 1\.10×（自动）/)).toBeTruthy()
  })
})

// ---------------------------------------------------------------- 编排账本
async function synthesizeOnce() {
  await enterTextStage()
  fireEvent.click(screen.getByText('生成候选音频'))
  await screen.findByText('编排账本')
  return document.querySelector('.plan')!.textContent ?? ''
}

describe('编排账本', () => {
  it('规则模式展示语音与静音的实际变化', async () => {
    const text = await synthesizeOnce()
    expect(text).toContain('全部由规则计算')
    expect(text).toContain('实际 1.93s / 目标 1.77s')
    expect(text).toContain('超出 0.16s')
    expect(text).toContain('尾部静音')
    expect(text).toContain('让出 0.09s 给语音')
  })

  it('AI 建议越界时明示被硬边界夹紧', async () => {
    vi.mocked(api.synthesize).mockResolvedValueOnce({
      ...M.synth,
      plan: {
        ...M.plan,
        source: 'llm',
        speed: { rules: 1.10, llm_raw: 1.25, applied: 1.10, clamped: true, source: 'llm' },
      },
    } as unknown as SynthResp)
    const text = await synthesizeOnce()
    expect(text).toContain('AI 已参与')
    expect(text).toContain('AI 建议 1.25×，已收敛到硬边界 1.10×')
    expect(text).toContain('边界生效')
  })

  it('AI 给出精简建议时可一键采用', async () => {
    vi.mocked(api.synthesize).mockResolvedValueOnce({
      ...M.synth,
      plan: { ...M.plan, source: 'llm', text_simplified: '国产舰载机' },
    } as unknown as SynthResp)
    await synthesizeOnce()
    expect(screen.getByText(/AI 建议精简为/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '采用' }))
    expect((document.querySelector('textarea') as HTMLTextAreaElement).value).toBe('国产舰载机')
  })
})

// ---------------------------------------------------------------- 纯音频项目
describe('纯音频项目', () => {
  it('不渲染视频画面，标题显示带后缀的文件名', async () => {
    vi.mocked(api.uploadVideo).mockResolvedValueOnce({ ...M.project, has_video: false })
    render(<App />)
    const input = document.querySelector('input[type=file]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['x'], 'a.mp3', { type: 'audio/mpeg' })] } })

    await screen.findByText('① 框口误区间')
    expect(document.querySelector('video')).toBeNull()
    expect(screen.getByText('test.mp4')).toBeTruthy() // mock 返回的名字带后缀
  })

  it('视频项目仍渲染视频画面', async () => {
    await enterWorkStage()
    expect(document.querySelector('video')).toBeTruthy()
  })
})
