import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const docsRoot = join(repositoryRoot, 'frontend', 'platform_docs')
const publicRoot = join(repositoryRoot, 'frontend', 'public')
const coveragePath = join(docsRoot, 'coverage.json')
const appSource = readFileSync(join(repositoryRoot, 'frontend', 'src', 'App.jsx'), 'utf8')
const adminPageSource = readFileSync(join(repositoryRoot, 'frontend', 'src', 'pages', 'AdminPage.jsx'), 'utf8')
const requiredFields = ['slug', 'title', 'description', 'order', 'read_time', 'icon']
const allowedRouteRoots = new Set([
  'admin', 'calendar', 'chat', 'clients', 'communications', 'contacts', 'guide', 'intake',
  'invoices', 'matters', 'onboarding', 'plugins', 'profile', 'reports', 'tasks',
  'teams', 'templates', 'time-tracking', 'trust',
])
const adminTabsBlock = adminPageSource.match(/const ADMIN_TABS = \[([\s\S]*?)\n\]/)?.[1] || ''
const adminTabs = new Set(Array.from(adminTabsBlock.matchAll(/id:\s*'([^']+)'/g), ([, tab]) => tab))

const errors = []
const seenSlugs = new Map()
const chapters = new Map()

function fail(file, message) {
  errors.push(`${file}: ${message}`)
}

function parseChapter(file, audience) {
  const source = readFileSync(file, 'utf8')
  const relativeFile = file.slice(repositoryRoot.length + 1).replaceAll('\\', '/')
  const match = source.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n/)
  if (!match) {
    fail(relativeFile, 'missing front matter')
    return
  }

  const metadata = {}
  for (const line of match[1].split(/\r?\n/)) {
    const separator = line.indexOf(':')
    if (separator !== -1) metadata[line.slice(0, separator).trim()] = line.slice(separator + 1).trim()
  }

  for (const field of requiredFields) {
    if (!metadata[field]) fail(relativeFile, `missing required field "${field}"`)
  }

  if (metadata.slug && !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(metadata.slug)) {
    fail(relativeFile, 'slug must be lowercase kebab-case')
  }
  if (metadata.slug && seenSlugs.has(`${audience}:${metadata.slug}`)) {
    fail(relativeFile, `duplicate slug also used by ${seenSlugs.get(`${audience}:${metadata.slug}`)}`)
  } else if (metadata.slug) {
    seenSlugs.set(`${audience}:${metadata.slug}`, relativeFile)
  }

  const numericOrder = Number(metadata.order)
  if (!Number.isInteger(numericOrder) || numericOrder <= 0) fail(relativeFile, 'order must be a positive integer')
  const filenameOrder = Number(file.split(/[\\/]/).at(-1).match(/^(\d+)-/)?.[1])
  if (Number.isFinite(filenameOrder) && numericOrder !== filenameOrder * 10) {
    fail(relativeFile, `order ${numericOrder} must match filename sequence ${filenameOrder}0`)
  }

  const content = source.slice(match[0].length).trim()
  if (metadata.slug) chapters.set(`${audience}:${metadata.slug}`, { content, relativeFile })
  if (!content.startsWith(`# ${metadata.title}`)) fail(relativeFile, 'first heading must match the title')
  if (!/^##\s+.+/m.test(content)) fail(relativeFile, 'chapter needs at least one level-two heading')

  const appLinks = Array.from(content.matchAll(/(?<!!)\[[^\]]+\]\((\/[^)\s]+)\)/g), ([, href]) => href)
  for (const href of appLinks) {
    const url = new URL(href, 'https://guide.invalid')
    const routeRoot = url.pathname.split('/').filter(Boolean)[0]
    if (!allowedRouteRoots.has(routeRoot)) fail(relativeFile, `unsupported in-app route ${href}`)
    if (audience === 'user' && ['admin', 'onboarding'].includes(routeRoot)) {
      fail(relativeFile, `user guide must not link to administrative route ${href}`)
    }
    if (routeRoot === 'admin') {
      const tab = url.searchParams.get('tab') || 'users'
      if (!adminTabs.has(tab)) fail(relativeFile, `unknown admin tab in ${href}`)
    }
  }

  const images = Array.from(content.matchAll(/!\[([^\]]*)\]\((\/guide-assets\/[^)\s]+)\)/g))
  for (const [, alt, href] of images) {
    if (!alt.trim()) fail(relativeFile, `image ${href} needs meaningful alternative text`)
    if (!existsSync(join(publicRoot, href))) fail(relativeFile, `image does not exist: ${href}`)
  }
}

for (const [directory, audience] of [['user-guide', 'user'], ['administrative-guide', 'admin']]) {
  const fullDirectory = join(docsRoot, directory)
  if (!existsSync(fullDirectory)) {
    fail(directory, 'required guide directory does not exist')
    continue
  }
  const files = readdirSync(fullDirectory).filter((name) => name.endsWith('.md')).sort()
  if (files.length === 0) fail(directory, 'guide must contain at least one Markdown chapter')
  files.forEach((file) => parseChapter(join(fullDirectory, file), audience))
}

if (!existsSync(join(docsRoot, 'README.md'))) fail('frontend/platform_docs', 'README.md is required')

function normalizeAuthenticatedRoute(route) {
  return route.replace(/\/:([^/]+)/g, '').replace(/\/$/, '') || '/'
}

function validateCoverage() {
  if (!existsSync(coveragePath)) {
    fail('frontend/platform_docs', 'coverage.json is required')
    return
  }

  let coverage
  try {
    coverage = JSON.parse(readFileSync(coveragePath, 'utf8'))
  } catch (error) {
    fail('frontend/platform_docs/coverage.json', `invalid JSON: ${error.message}`)
    return
  }

  const userModules = Array.isArray(coverage.user_modules) ? coverage.user_modules : []
  const coveredUserRoutes = new Set()
  const coveredUserIds = new Set()
  for (const entry of userModules) {
    if (!entry?.id || !entry?.route || !entry?.chapter) {
      fail('frontend/platform_docs/coverage.json', 'every user module needs id, route, and chapter')
      continue
    }
    if (coveredUserIds.has(entry.id)) fail('frontend/platform_docs/coverage.json', `duplicate user module id ${entry.id}`)
    if (coveredUserRoutes.has(entry.route)) fail('frontend/platform_docs/coverage.json', `duplicate user route ${entry.route}`)
    coveredUserIds.add(entry.id)
    coveredUserRoutes.add(entry.route)
    const chapter = chapters.get(`user:${entry.chapter}`)
    if (!chapter) {
      fail('frontend/platform_docs/coverage.json', `user module ${entry.id} references unknown chapter ${entry.chapter}`)
    } else if (!chapter.content.includes(`](${entry.route})`)) {
      fail(chapter.relativeFile, `coverage chapter must link to ${entry.route} for module ${entry.id}`)
    }
  }

  const authenticatedSection = appSource
    .split('{/* Authenticated pages wrapped in AppShell */}')[1]
    ?.split('{/* Admin routes */}')[0] || ''
  const excludedUserRoutes = new Set(['/billing', '/guide', '/teams/config'])
  const applicationUserRoutes = new Set(
    Array.from(authenticatedSection.matchAll(/path="([^"]+)"/g), ([, route]) => normalizeAuthenticatedRoute(route))
      .filter((route) => !excludedUserRoutes.has(route)),
  )
  for (const route of applicationUserRoutes) {
    if (!coveredUserRoutes.has(route)) fail('frontend/platform_docs/coverage.json', `authenticated product route is undocumented: ${route}`)
  }
  for (const route of coveredUserRoutes) {
    if (!applicationUserRoutes.has(route)) fail('frontend/platform_docs/coverage.json', `user module route is not registered in App.jsx: ${route}`)
  }

  const adminEntries = Array.isArray(coverage.admin_tabs) ? coverage.admin_tabs : []
  const coveredAdminTabs = new Set()
  for (const entry of adminEntries) {
    if (!entry?.tab || !entry?.chapter) {
      fail('frontend/platform_docs/coverage.json', 'every admin tab needs tab and chapter')
      continue
    }
    if (coveredAdminTabs.has(entry.tab)) fail('frontend/platform_docs/coverage.json', `duplicate admin tab ${entry.tab}`)
    coveredAdminTabs.add(entry.tab)
    const chapter = chapters.get(`admin:${entry.chapter}`)
    if (!chapter) {
      fail('frontend/platform_docs/coverage.json', `admin tab ${entry.tab} references unknown chapter ${entry.chapter}`)
    } else if (!chapter.content.includes(`](/admin?tab=${entry.tab})`)) {
      fail(chapter.relativeFile, `coverage chapter must link to /admin?tab=${entry.tab}`)
    }
  }
  for (const tab of adminTabs) {
    if (!coveredAdminTabs.has(tab)) fail('frontend/platform_docs/coverage.json', `admin tab is undocumented: ${tab}`)
  }
  for (const tab of coveredAdminTabs) {
    if (!adminTabs.has(tab)) fail('frontend/platform_docs/coverage.json', `admin tab is not registered in AdminPage.jsx: ${tab}`)
  }
}

validateCoverage()

if (errors.length) {
  console.error(`Platform documentation check failed with ${errors.length} error(s):`)
  errors.forEach((error) => console.error(`- ${error}`))
  process.exit(1)
}

console.log(`Platform documentation check passed (${seenSlugs.size} chapters; all product routes and admin tabs covered).`)
