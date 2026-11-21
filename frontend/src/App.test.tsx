import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { FakeAudio } from './test/setup'

// mock 网络层：上传即返回固定项目
vi.mock('./api', () => ({
  api: {
    uploadVideo: vi.fn(async () => ({
      id: 'p1', name: 'test', duration: 10,
      video_url: '/data/p1/source.mp4', audio_url: '/data/p1/audio.wav',
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
