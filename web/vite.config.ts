import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    host: '0.0.0.0',  // 监听所有网卡，让 WSL 内启动的 vite 能被 Windows 访问
    port: 5173,
    strictPort: true,
  },
})
