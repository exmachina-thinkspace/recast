import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'

const lensBridgeTarget = process.env.LENS_BRIDGE_TARGET || 'http://172.16.94.151:8910'
const https =
  process.env.HTTPS_KEY && process.env.HTTPS_CERT
    ? {
        key: fs.readFileSync(process.env.HTTPS_KEY),
        cert: fs.readFileSync(process.env.HTTPS_CERT),
      }
    : undefined

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    https,
    proxy: {
      '/api/recast-lens': {
        target: lensBridgeTarget,
        changeOrigin: true,
      },
      '/health': {
        target: lensBridgeTarget,
        changeOrigin: true,
      },
    },
  },
})
