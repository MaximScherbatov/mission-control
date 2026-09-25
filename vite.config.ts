import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        ws: true,
      },
      '/docs': 'http://127.0.0.1:8765',
      '/redoc': 'http://127.0.0.1:8765',
      '/openapi.json': 'http://127.0.0.1:8765',
    },
  },
})
