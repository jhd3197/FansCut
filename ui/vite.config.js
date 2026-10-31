import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// `npm run dev` proxies /api to a running `fanscut serve`; `npm run build`
// writes ui/dist, which the FastAPI app serves.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5464,
    proxy: { '/api': { target: process.env.FANSCUT_API || 'http://127.0.0.1:6464', changeOrigin: true } },
  },
  build: { outDir: 'dist', emptyOutDir: true },
  test: { environment: 'jsdom', globals: false },
});
