/**
 * E2E：真实输入事件驱动完整试听链路（后端用本地服务，上传/抽轨走真 ffmpeg）。
 * 覆盖：试听自动停止后重点（bug1）、播完才停不早停（bug2）、暂停续播、
 * 重新上传状态重置、进度条游标与波形播放头对齐。
 */
import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'

const VIDEO = path.resolve(process.cwd(), '../test/350679646.mp4')

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
