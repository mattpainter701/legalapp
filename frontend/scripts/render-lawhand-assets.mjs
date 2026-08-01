import { chromium } from '@playwright/test'
import { copyFile, mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repository = path.resolve(frontend, '..')
const brand = path.join(frontend, 'public', 'brand', 'lawhand')

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage()

async function render(sourceName, outputPath, width, height, omitBackground = false) {
  const svg = await readFile(path.join(brand, sourceName), 'utf8')
  await mkdir(path.dirname(outputPath), { recursive: true })
  await page.setViewportSize({ width, height })
  await page.setContent(`<!doctype html><style>html,body{margin:0;width:100%;height:100%;overflow:hidden}svg{display:block;width:100%;height:100%}</style>${svg}`)
  await page.screenshot({ path: outputPath, omitBackground })
}

for (const size of [72, 96, 128, 144, 152, 192, 384, 512]) {
  await render(
    'lawhand-mark.svg',
    path.join(frontend, 'public', 'icons', `icon-${size}x${size}.png`),
    size,
    size,
  )
}

for (const size of [16, 32, 64, 80]) {
  await render(
    'lawhand-mark.svg',
    path.join(repository, 'word-addin', 'assets', `icon-${size}.png`),
    size,
    size,
  )
}

await render('lawhand-mark.svg', path.join(brand, 'icon-512.png'), 512, 512)
await render('lawhand-social-card.svg', path.join(brand, 'lawhand-social-card.png'), 1200, 630)
await copyFile(path.join(brand, 'lawhand-social-card.png'), path.join(frontend, 'public', 'social-card-v2.png'))
await render('lawhand-email-header.svg', path.join(brand, 'lawhand-email-header.png'), 1200, 280)
await render('lawhand-document-header.svg', path.join(brand, 'lawhand-document-header.png'), 1200, 200)
await render('lawhand-linkedin-cover.svg', path.join(brand, 'lawhand-linkedin-cover.png'), 1584, 396)
await render('lawhand-mark.svg', path.join(repository, 'teams-app', 'color.png'), 192, 192)
await render('lawhand-outline.svg', path.join(repository, 'teams-app', 'outline.png'), 32, 32, true)

await browser.close()
