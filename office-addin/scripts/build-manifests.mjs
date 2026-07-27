import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const origin = (process.env.OFFICE_ADDIN_ORIGIN || 'https://localhost:3001').replace(/\/$/, '')
const parsed = new URL(origin)

if (parsed.protocol !== 'https:' || parsed.origin !== origin) {
  throw new Error('OFFICE_ADDIN_ORIGIN must be an HTTPS origin without a path, query, or fragment')
}

const outputDirectory = path.join(root, 'dist', 'manifests')
await mkdir(outputDirectory, { recursive: true })

for (const filename of ['icon-96x96.png', 'icon-128x128.png']) {
  await copyFile(
    path.join(root, '..', 'frontend', 'public', 'icons', filename),
    path.join(root, 'dist', filename),
  )
}

for (const filename of ['word-excel.xml', 'outlook.xml']) {
  const template = await readFile(path.join(root, 'manifests', filename), 'utf8')
  const rendered = template.replaceAll('__APP_ORIGIN__', origin)
  if (rendered.includes('__APP_ORIGIN__')) throw new Error(`Unresolved origin placeholder in ${filename}`)
  await writeFile(path.join(outputDirectory, filename), rendered, 'utf8')
}
