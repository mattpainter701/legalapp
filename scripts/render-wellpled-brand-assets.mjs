import { access, mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(scriptDir, '..')
const brandDir = path.join(repoRoot, 'frontend', 'public', 'brand', 'wellpled')
const frontendRequire = createRequire(path.join(repoRoot, 'frontend', 'package.json'))
const { chromium } = frontendRequire('playwright')

const renders = [
  ['wellpled-mark.svg', 'wellpled-mark-preview.png', 512, 512],
  ['favicon.svg', 'favicon-16.png', 16, 16],
  ['favicon.svg', 'favicon-32.png', 32, 32],
  ['favicon.svg', 'icon-72.png', 72, 72],
  ['favicon.svg', 'icon-96.png', 96, 96],
  ['favicon.svg', 'icon-128.png', 128, 128],
  ['favicon.svg', 'icon-144.png', 144, 144],
  ['favicon.svg', 'icon-152.png', 152, 152],
  ['favicon.svg', 'apple-touch-icon.png', 180, 180],
  ['favicon.svg', 'icon-192.png', 192, 192],
  ['favicon.svg', 'icon-384.png', 384, 384],
  ['favicon.svg', 'icon-512.png', 512, 512],
  ['wellpled-logo-horizontal.svg', 'wellpled-logo-horizontal.png', 1200, 280],
  ['wellpled-logo-horizontal-reversed.svg', 'wellpled-logo-horizontal-reversed.png', 1200, 280],
  ['wellpled-social-card.svg', 'wellpled-social-card.png', 1200, 630],
  ['wellpled-linkedin-cover.svg', 'wellpled-linkedin-cover.png', 1584, 396],
  ['wellpled-email-header.svg', 'wellpled-email-header.png', 1200, 280],
  ['wellpled-document-header.svg', 'wellpled-document-header.png', 1200, 200],
  ['wellpled-brand-sheet.svg', 'wellpled-brand-sheet.png', 1600, 1000],
]

await mkdir(brandDir, { recursive: true })

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } })

try {
  for (const [sourceName, outputName, width, height] of renders) {
    const sourcePath = path.join(brandDir, sourceName)
    try {
      await access(sourcePath)
    } catch {
      continue
    }

    await page.setViewportSize({ width, height })
    await page.goto(pathToFileURL(sourcePath).href, { waitUntil: 'networkidle' })
    await page.evaluate(() => {
      document.documentElement.style.margin = '0'
      document.documentElement.style.padding = '0'
      document.documentElement.style.background = 'transparent'
      if (document.body) {
        document.body.style.margin = '0'
        document.body.style.padding = '0'
        document.body.style.background = 'transparent'
        document.body.style.overflow = 'hidden'
      }
      const svg = document.querySelector('svg')
      if (svg) {
        svg.style.display = 'block'
        svg.style.width = '100vw'
        svg.style.height = '100vh'
      }
    })

    await page.screenshot({
      path: path.join(brandDir, outputName),
      omitBackground: true,
    })
  }

  const previewPath = path.join(brandDir, 'index.html')
  try {
    await access(previewPath)
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(pathToFileURL(previewPath).href, { waitUntil: 'networkidle' })
    await page.screenshot({
      path: path.join(brandDir, 'wellpled-brand-preview.png'),
      fullPage: true,
    })
  } catch {
    // The preview page is optional while the kit is being assembled.
  }
} finally {
  await browser.close()
}
