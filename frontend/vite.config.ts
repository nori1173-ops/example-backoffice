import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'

export default defineConfig({
  plugins: [
    vue(),
    vuetify({ autoImport: true, styles: { configFile: 'src/styles/settings.scss' } }),
  ],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'https://backoffice-dev.example-cloud.com',
        changeOrigin: true,
      },
    },
  },
  build: { sourcemap: false, chunkSizeWarningLimit: 1200 },
})
