import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The project previously ran with no config at all, which worked for builds but
// gave no React Fast Refresh during development. Registering the plugin
// explicitly also pins the dev server port so the backend CORS allow-list
// (CORS_ORIGINS) stays accurate.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true
  },
  build: {
    outDir: 'dist',
    sourcemap: false
  }
});
