import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Le proxy Vite ne s'applique qu'au serveur local de développement.
// En production, Nginx sert dist/ et relaie /api/ vers Gunicorn.
export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8172', changeOrigin: false },
    },
  },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true },
  build: { outDir: 'dist', sourcemap: false, target: 'es2022', emptyOutDir: true },
})
