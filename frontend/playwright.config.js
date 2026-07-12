import { defineConfig, devices } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const frontendDir = path.dirname(fileURLToPath(import.meta.url))
const backendDir = path.resolve(frontendDir, '..', 'backend')
const python = process.env.PYTHON || 'python'
const frontendPort = Number(process.env.E2E_FRONTEND_PORT || 3000)
const backendPort = Number(process.env.E2E_BACKEND_PORT || 8000)
const frontendOrigin = `http://127.0.0.1:${frontendPort}`
const backendOrigin = `http://127.0.0.1:${backendPort}`
const reuseExistingServers = process.env.E2E_REUSE_EXISTING_SERVERS === 'true'
const runtimeEnv = { ...process.env }
delete runtimeEnv.E2E_ADMIN_DATABASE_URL

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.js',
  outputDir: 'test-results',
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI
    ? [['line'], ['html', { outputFolder: 'playwright-report', open: 'never' }]]
    : [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  use: {
    baseURL: frontendOrigin,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: [
    {
      command: `${python} -m uvicorn app.main:app --host 127.0.0.1 --port ${backendPort}`,
      cwd: backendDir,
      env: {
        ...runtimeEnv,
        RUN_SCHEDULER: 'false',
        LITELLM_ENABLED: 'false',
        FRONTEND_URL: frontendOrigin,
        BACKEND_URL: backendOrigin,
      },
      url: `${backendOrigin}/health`,
      reuseExistingServer: reuseExistingServers,
      timeout: 120_000,
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${frontendPort} --strictPort`,
      cwd: frontendDir,
      env: {
        ...runtimeEnv,
        VITE_PROXY_TARGET: backendOrigin,
      },
      url: frontendOrigin,
      reuseExistingServer: reuseExistingServers,
      timeout: 120_000,
    },
  ],
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
