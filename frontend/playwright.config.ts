import { defineConfig } from '@playwright/test'

// E2E：对真实后端（127.0.0.1:8765）跑真 Chromium。
// 已有服务在跑则复用；否则自动用仓库 venv 拉起 python run.py。
export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:8765',
    viewport: { width: 1280, height: 900 },
  },
  webServer: {
    command: '.venv\\Scripts\\python.exe run.py',
    cwd: '..',
    url: 'http://127.0.0.1:8765/api/health',
    reuseExistingServer: true,
    timeout: 60_000,
  },
})
