import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backend = 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/auth': backend,
      '/dashboard': backend,
      '/reconciliation': backend,
      '/transactions': backend,
      '/forecast': backend,
      '/anomalies': backend,
      '/demo': backend,
      '/reports': backend,
      '/webhooks': backend,
      '/health': backend,
    },
  },
});
