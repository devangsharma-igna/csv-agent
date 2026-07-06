import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

declare const process: {
  env: Record<string, string | undefined>;
};

const previewAllowedHosts = [
  ...(process.env.PREVIEW_ALLOWED_HOSTS ?? '')
    .split(',')
    .map(host => host.trim())
    .filter(Boolean),
  process.env.RAILWAY_PUBLIC_DOMAIN,
].filter((host): host is string => Boolean(host));

export default defineConfig({
  plugins: [react()],
  preview: {
    allowedHosts: previewAllowedHosts,
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
});
