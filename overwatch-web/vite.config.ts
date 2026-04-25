import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Base path matters: served at platform.vaultscaler.com/engineering/
export default defineConfig({
  plugins: [react()],
  base: '/engineering/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://localhost:9001',
        changeOrigin: true,
      },
    },
  },
});
