import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const frontendDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const backendDir = path.resolve(frontendDir, '..', 'backend')
const python = process.env.PYTHON || 'python'
const prepareEnv = {
  ...process.env,
  // CI serves requests through a NOBYPASSRLS role, while schema/fixture setup
  // remains an explicitly separate owner operation.
  DATABASE_URL: process.env.E2E_ADMIN_DATABASE_URL || process.env.DATABASE_URL,
}

function run(args) {
  const result = spawnSync(python, args, {
    cwd: backendDir,
    env: prepareEnv,
    stdio: 'inherit',
    shell: false,
  })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}

run(['-m', 'alembic', 'upgrade', 'head'])
run(['-m', 'scripts.seed_e2e'])
