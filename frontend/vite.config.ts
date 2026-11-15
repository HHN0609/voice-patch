import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 构建产物输出到后端静态目录 web/，由 FastAPI /ui 挂载
export default defineConfig({
  plugins: [react()],
  base: '/ui/',
  build: {
    outDir: '../web',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/data': 'http://127.0.0.1:8765',
    },
  },
})
