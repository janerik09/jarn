import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    middlewareMode: false,
    // Same-origin API: the UI calls /api/v1 and Vite forwards it (including the job WebSocket) to the backend
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true,
        ws: true,
      },
    },
    // Extra hostnames the dev server may be reached by, comma separated. Set VITE_ALLOWED_HOSTS
    // and VITE_HMR_HOST in the environment instead of editing this file.
    allowedHosts: [
      'localhost',
      '127.0.0.1',
      '0.0.0.0',
      ...(process.env.VITE_ALLOWED_HOSTS ? process.env.VITE_ALLOWED_HOSTS.split(',').map(h => h.trim()) : []),
    ],
    hmr: {
      host: process.env.VITE_HMR_HOST || 'localhost',
      port: 5173,
      protocol: 'ws',
    },
  },
  build: {
    outDir: 'dist',
  },
})
