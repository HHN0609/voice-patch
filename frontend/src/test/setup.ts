/**
 * 单测环境：mock 浏览器媒体与动画 API，让 previewRange 状态机可确定性复现竞态时序。
 * - FakeAudio：可控 currentTime / seeking / play·pause，手动派发事件
 * - 手动 rAF 队列：测试里调用 window.__flushRaf() 驱动每一帧
 */

type Listener = (ev?: unknown) => void

class FakeAudio {
  static instances: FakeAudio[] = []

  src = ''
  paused = true
  seeking = false
  ontimeupdate: Listener | null = null
  onended: Listener | null = null
  playCount = 0
  // 为 true 时 currentTime 赋值不立即生效（模拟异步 seek），需手动 finishSeek()
  asyncSeek = false
  #currentTime = 0
  #pendingSeek: number | null = null
  #listeners = new Map<string, Listener[]>()

  constructor() {
    FakeAudio.instances.push(this)
  }

  get currentTime() {
    // worst-case 模拟：seek 未完成时 getter 仍返回旧位置（真实浏览器可能如此）
    return this.#currentTime
  }

  set currentTime(t: number) {
    if (this.asyncSeek) {
      this.seeking = true
      this.#pendingSeek = t
    } else {
      this.#currentTime = t
    }
  }

  // asyncSeek 模式下：让 seek 生效，触发 seeked 事件
  finishSeek() {
    if (this.#pendingSeek == null) return
    this.#currentTime = this.#pendingSeek
    this.#pendingSeek = null
    this.seeking = false
    this.dispatch('seeked')
  }

  play() {
    this.playCount++
    this.paused = false
    return Promise.resolve()
  }

  pause() {
    this.paused = true
  }

  addEventListener(type: string, fn: Listener) {
    const list = this.#listeners.get(type) ?? []
    list.push(fn)
    this.#listeners.set(type, list)
  }

  removeEventListener(type: string, fn: Listener) {
    this.#listeners.set(type, (this.#listeners.get(type) ?? []).filter((f) => f !== fn))
  }

  dispatch(type: string) {
    if (type === 'timeupdate') this.ontimeupdate?.()
    if (type === 'ended') this.onended?.()
    for (const fn of this.#listeners.get(type) ?? []) fn()
  }
}

const rafCbs = new Map<number, FrameRequestCallback>()
let rafId = 0

window.Audio = FakeAudio as unknown as typeof Audio
// jsdom 未实现媒体方法（<video> 的播放控制），打桩避免 "Not implemented" 噪音
window.HTMLMediaElement.prototype.play = function () { return Promise.resolve() }
window.HTMLMediaElement.prototype.pause = function () { /* noop */ }
window.requestAnimationFrame = (cb) => {
  rafId++
  rafCbs.set(rafId, cb)
  return rafId
}
window.cancelAnimationFrame = (id) => { rafCbs.delete(id) }
// 手动推进一帧；返回剩余帧数（0 表示跟随循环已停止）
;(window as unknown as Record<string, unknown>).__flushRaf = (t = 0) => {
  const cbs = [...rafCbs.values()]
  rafCbs.clear()
  cbs.forEach((cb) => cb(t))
  return rafCbs.size
}

export { FakeAudio }
