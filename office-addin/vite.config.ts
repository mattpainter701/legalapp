import { defineConfig } from 'vitest/config'

export default defineConfig({
  base: '/office/',
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  server: {
    port: 3001,
    strictPort: true,
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
})
