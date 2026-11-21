/**
 * E2E：真实输入事件驱动完整试听链路（后端用本地服务，上传/抽轨走真 ffmpeg）。
 * 覆盖：试听自动停止后重点（bug1）、播完才停不早停（bug2）、暂停续播、
 * 重新上传状态重置、进度条游标与波形播放头对齐、语速三态与编排账本、纯音频上传。
 *
 * 说明：ASR / TTS 会真实调用云端接口（耗时且要 Key），凡涉及这两步的用例
 * 都用 page.route 打桩，只保留上传/抽轨/探测等本地 ffmpeg 链路走真后端。
 */
import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'

const VIDEO = path.resolve(process.cwd(), '../test-files/350679646.mp4')

/** "m:ss.xx" → 秒 */
const parse = (s: string) => {
  const m = s.match(/(\d+):(\d+(?:\.\d+)?)/)!
  return Number(m[1]) * 60 + Number(m[2])
}

const timeText = (page: Page) => page.locator('.time').textContent() as Promise<string>

async function upload(page: Page) {
  await page.goto('/ui/')
  await page.setInputFiles('input[type=file]', VIDEO)
  // 波形就绪：进度条解禁
  await page.waitForFunction(() => {
    const el = document.querySelector<HTMLInputElement>('.scrubber')
    return !!el && !el.disabled
  }, undefined, { timeout: 30_000 })
}

/** 在波形上按比例拖拽框选口误区间，返回解析出的 [t0, t1] */
async function dragRegion(page: Page, f0: number, f1: number): Promise<[number, number]> {
  const box = await page.locator('.waveform-holder').boundingBox()
  const y = box!.y + box!.height / 2
  await page.mouse.move(box!.x + box!.width * f0, y)
  await page.mouse.down()
  for (let i = 1; i <= 10; i++) {
    await page.mouse.move(box!.x + box!.width * (f0 + ((f1 - f0) * i) / 10), y)
  }
  await page.mouse.up()
  const label = await page.locator('.sel-info span').first().textContent()
  const m = label!.match(/(\d+:\d+(?:\.\d+)?) → (\d+:\d+(?:\.\d+)?)/)!
  return [parse(m[1]), parse(m[2])]
}

const previewBtn = (page: Page) =>
  page.locator('.sel-info button').first()

/** 等待自动停止（按钮回到 ▶ 试听），返回停止时刻的光标位置 */
async function waitAutoStop(page: Page, withinSec: number) {
  await expect(previewBtn(page)).toContainText('▶ 试听', { timeout: withinSec * 1000 + 8000 })
}

test('试听播完自动停，重点试听从 t0 完整播到 t1（bug1/bug2）', async ({ page }) => {
  await upload(page)
  const [t0, t1] = await dragRegion(page, 0.15, 0.3)
  expect(t1 - t0).toBeGreaterThan(2) // 选区足够长，断言才可靠

  // 第一次试听 → 自动停
  await previewBtn(page).click()
  await expect(previewBtn(page)).toContainText('■ 停止')
  await waitAutoStop(page, t1 - t0)
  const stopAt = parse(await timeText(page))
  expect(Math.abs(stopAt - t1)).toBeLessThan(0.5) // 停在选区终点附近

  // 重点试听：必须立即从 t0 起播，且后续 1.5s 内持续播放不误停；视频画面同步跟随
  await previewBtn(page).click()
  await expect(previewBtn(page)).toContainText('■ 停止')
  const startAt = parse(await timeText(page))
  expect(Math.abs(startAt - t0)).toBeLessThan(0.3)
  let last = startAt
  for (let i = 0; i < 10; i++) {
    await page.waitForTimeout(150)
    await expect(previewBtn(page)).toContainText('■ 停止') // bug1: 一小段就停
    const now = parse(await timeText(page))
    expect(now).toBeGreaterThan(last - 0.001) // 持续前进
    last = now
    const vt = await page.evaluate(() => (document.querySelector('video') as HTMLVideoElement).currentTime)
    expect(vt).toBeGreaterThan(t0) // 视频画面跟随试听前进
  }
  // 播到选区终点才自动停（bug2: 没播完就停）；停止后视频一并暂停且不超前（光标不越界）
  await waitAutoStop(page, t1 - t0)
  const finalAt = parse(await timeText(page))
  expect(Math.abs(finalAt - t1)).toBeLessThan(0.3)
  const v = await page.evaluate(() => {
    const el = document.querySelector('video') as HTMLVideoElement
    return { t: el.currentTime, paused: el.paused }
  })
  expect(v.paused).toBe(true)
  expect(Math.abs(v.t - finalAt)).toBeLessThan(0.15) // 视频与试听音频相位同步
})

test('试听中途停止，再点试听从暂停位置续播', async ({ page }) => {
  await upload(page)
  const [t0, t1] = await dragRegion(page, 0.15, 0.3)
  await previewBtn(page).click()
  await expect(previewBtn(page)).toContainText('■ 停止')
  await page.waitForTimeout(1200)
  await previewBtn(page).click() // 停止
  await expect(previewBtn(page)).toContainText('▶ 试听')
  const pausedAt = parse(await timeText(page))
  expect(pausedAt).toBeGreaterThan(t0 + 0.3)
  expect(pausedAt).toBeLessThan(t1 - 0.3)

  // 续播：起点 ≈ 暂停位置
  await previewBtn(page).click()
  await expect(previewBtn(page)).toContainText('■ 停止')
  const resumedAt = parse(await timeText(page))
  expect(Math.abs(resumedAt - pausedAt)).toBeLessThan(0.4)
  await waitAutoStop(page, t1 - resumedAt)
})

test('重新上传后全部状态回到默认', async ({ page }) => {
  await upload(page)
  await dragRegion(page, 0.15, 0.3)
  await page.getByRole('button', { name: '只读浏览' }).click()
  await page.getByRole('button', { name: /播放/ }).first().click() // 主播放中
  await page.getByRole('button', { name: '重新上传' }).click()
  await expect(page.getByText(/点击选择/)).toBeVisible()

  await page.setInputFiles('input[type=file]', VIDEO)
  await page.waitForFunction(() => {
    const el = document.querySelector<HTMLInputElement>('.scrubber')
    return !!el && !el.disabled
  }, undefined, { timeout: 30_000 })
  await expect(page.getByRole('button', { name: '① 框口误区间' })).toHaveClass(/on/)
  await expect(page.getByText(/口误区间：未选/)).toBeVisible()
  const t = await page.locator('.scrubber').inputValue()
  expect(Number(t)).toBe(0)
})

test('点击波形跳转后，进度条滑块盒与波形容器精确对齐', async ({ page }) => {
  await upload(page)
  const box = await page.locator('.waveform-holder').boundingBox()
  await page.mouse.click(box!.x + box!.width * 0.5, box!.y + box!.height / 2)
  await page.waitForTimeout(300)
  const dur = parse(await timeText(page)) // "0:12.95 / 0:25.91"
  const total = parse((await timeText(page)).split('/')[1])
  expect(Math.abs(dur - total / 2)).toBeLessThan(0.5)

  // CSS 修复断言：滑块盒子两侧各外扩 7px，恰好覆盖波形容器外框
  const geo = await page.evaluate(() => {
    const s = document.querySelector('.scrubber')!.getBoundingClientRect()
    const w = document.querySelector('.waveform-holder')!.getBoundingClientRect()
    return {
      left: Math.abs(s.left - w.left),
      right: Math.abs(s.left + s.width - (w.left + w.width)),
    }
  })
  expect(geo.left).toBeLessThan(1)
  expect(geo.right).toBeLessThan(1)
})

// ------------------------------------------------------------ 语速三态 / 纯音频

/** 转写与合成的桩响应：字段与后端返回保持一致 */
const SEG = {
  segment_id: 'e2e-seg',
  t0: 4.96, t1: 6.98, T0: 4.96, T1: 6.98, window: 2.02, speech: 1.77,
  words: [{ text: '国产', start: 0.1, end: 0.4, confidence: 1 }],
  text_raw: '国产新型舰载机', text_norm: '国产新型舰载机',
}

const SYNTH = {
  segment_id: 'e2e-seg', text_norm: '国产新型舰载机', speed: 1.1,
  window: 2.02, speech: 1.77,
  takes: [{
    take_id: 't1', take_no: 1, final_dur: 2.02, delta: 0, stretch: 1,
    status: 'ok', speed: 1.1, seed: 0, wav: '/data/e2e/t1.wav',
  }],
  voice_source: 'mispeak', overflow: false, delta: 0, d_class: false,
  plan: {
    source: 'rules', reason: '', text_simplified: null,
    speed: { rules: 1.1, llm_raw: null, applied: 1.1, clamped: false, source: 'rules' },
    pad_side: { rules: 'tail', llm_raw: null, applied: 'tail', source: 'rules' },
    speech: { target: 1.77, body: 1.93, delta: 0.16 },
    silence: { head_before: 0.16, head_after: 0.09, tail_before: 0.09, tail_after: 0 },
  },
}

/** 桩掉 ASR / TTS，避免 E2E 触发真实云端调用 */
async function mockCloud(page: Page) {
  await page.route('**/api/projects/*/transcribe', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SEG) }))
  await page.route('**/api/segments/*/synthesize', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SYNTH) }))
}

/** 内存构造 3s / 16kHz / 单声道 / 16-bit PCM 正弦 WAV，用于纯音频上传 */
function wav3s(): Buffer {
  const rate = 16000
  const n = rate * 3
  const buf = Buffer.alloc(44 + n * 2)
  buf.write('RIFF', 0)
  buf.writeUInt32LE(36 + n * 2, 4)
  buf.write('WAVE', 8)
  buf.write('fmt ', 12)
  buf.writeUInt32LE(16, 16)
  buf.writeUInt16LE(1, 20)        // PCM
  buf.writeUInt16LE(1, 22)        // 单声道
  buf.writeUInt32LE(rate, 24)
  buf.writeUInt32LE(rate * 2, 28) // byte rate
  buf.writeUInt16LE(2, 32)        // block align
  buf.writeUInt16LE(16, 34)       // bits
  buf.write('data', 36)
  buf.writeUInt32LE(n * 2, 40)
  for (let i = 0; i < n; i++) {
    buf.writeInt16LE(Math.round(Math.sin((2 * Math.PI * 220 * i) / rate) * 12000), 44 + i * 2)
  }
  return buf
}

test('语速三态：AI 决定 / 自动 / 手动 互斥切换，账本标注来源', async ({ page }) => {
  await mockCloud(page)
  await upload(page)
  await dragRegion(page, 0.15, 0.3)
  await page.getByRole('button', { name: '识别此区间' }).click()
  await expect(page.getByText('② 修改文本')).toBeVisible()

  // 默认「自动」：没有「AI 决定」选项、不渲染滑块，只显示待计算
  await expect(page.getByRole('button', { name: '自动' })).toHaveClass(/on/)
  await expect(page.getByRole('button', { name: 'AI 决定' })).toHaveCount(0)
  await expect(page.locator('.speed-line input[type=range]')).toHaveCount(0)
  await expect(page.getByText('待计算')).toBeVisible()

  // 手动 → 出现语速滑块
  await page.getByRole('button', { name: '手动' }).click()
  await expect(page.locator('.speed-line input[type=range]')).toBeVisible()

  // 打开 AI 决策 → 语速默认交给 AI（复选框视觉隐藏，点开关本体）
  await page.locator('.switch').click()
  await expect(page.locator('.switch input')).toBeChecked()
  await expect(page.getByRole('button', { name: 'AI 决定' })).toHaveClass(/on/)
  await expect(page.getByRole('button', { name: '自动' })).not.toHaveClass(/on/)
  await expect(page.getByText('由 AI 决定')).toBeVisible()

  // 关闭 AI 决策 → 「AI 决定」消失并回落到「自动」
  await page.locator('.switch').click()
  await expect(page.locator('.switch input')).not.toBeChecked()
  await expect(page.getByRole('button', { name: 'AI 决定' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '自动' })).toHaveClass(/on/)

  // 生成候选 → 账本展示规则来源、实际时长与静音变化
  await page.getByRole('button', { name: '生成候选音频' }).click()
  const plan = page.locator('.plan')
  await expect(plan).toContainText('全部由规则计算')
  await expect(plan).toContainText('1.00 → 1.10×（加快 10%）')
  await expect(plan).toContainText('实际 1.93s / 目标 1.77s（超出 0.16s）')
  await expect(plan).toContainText('0.09s → 0.00s（让出 0.09s 给语音）')
})

test('纯音频上传：不渲染画面，后端补黑底占位轨后全链路可用', async ({ page }) => {
  await mockCloud(page)
  await page.goto('/ui/')
  await page.setInputFiles('input[type=file]', {
    name: 'voice-only.wav', mimeType: 'audio/wav', buffer: wav3s(),
  })
  await page.waitForFunction(() => {
    const el = document.querySelector<HTMLInputElement>('.scrubber')
    return !!el && !el.disabled
  }, undefined, { timeout: 30_000 })

  // 文件名保留后缀；纯音频不渲染 <video> 画面
  await expect(page.locator('.proj-name')).toHaveText('voice-only.wav')
  await expect(page.locator('video')).toHaveCount(0)
  await expect(page.locator('.time')).toContainText('0:03')

  // 后端标记 has_video=false，并生成了占位视频轨（下游导出仍走 mp4）
  const info = await page.evaluate(async () => {
    const list = (await (await fetch('/api/projects')).json()) as Array<Record<string, unknown>>
    return list.find((p) => p.name === 'voice-only.wav')
  })
  expect(info?.has_video).toBe(false)
  expect(String(info?.video_url)).toContain('source.mp4')

  // 纯音频同样能框选 + 识别（ASR 走桩）
  await dragRegion(page, 0.2, 0.5)
  await page.getByRole('button', { name: '识别此区间' }).click()
  await expect(page.getByText('② 修改文本')).toBeVisible()
})
