const userGuideModules = import.meta.glob('../platform_docs/user-guide/*.md', {
  query: '?raw',
  import: 'default',
  eager: true,
})

const administrativeGuideModules = import.meta.glob('../platform_docs/administrative-guide/*.md', {
  query: '?raw',
  import: 'default',
  eager: true,
})

const REQUIRED_FIELDS = ['slug', 'title', 'description', 'order', 'read_time', 'icon']

export function parseGuideChapter(source, sourcePath, audience) {
  const match = source.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n/)
  if (!match) throw new Error(`Guide chapter is missing front matter: ${sourcePath}`)

  const metadata = {}
  for (const line of match[1].split(/\r?\n/)) {
    const separator = line.indexOf(':')
    if (separator === -1) continue
    metadata[line.slice(0, separator).trim()] = line.slice(separator + 1).trim()
  }

  for (const field of REQUIRED_FIELDS) {
    if (!metadata[field]) throw new Error(`Guide chapter ${sourcePath} is missing ${field}`)
  }

  const content = source.slice(match[0].length).trim()
  const headings = Array.from(content.matchAll(/^##\s+(.+)$/gm), ([, title]) => ({
    title: title.replace(/[*_`]/g, '').trim(),
    id: slugifyHeading(title),
  }))

  return {
    ...metadata,
    audience,
    order: Number(metadata.order),
    content,
    headings,
    sourcePath,
    searchText: `${metadata.title} ${metadata.description} ${content}`.toLocaleLowerCase(),
  }
}

export function slugifyHeading(value) {
  const text = Array.isArray(value) ? value.join(' ') : String(value || '')
  return text
    .replace(/[*_`]/g, '')
    .toLocaleLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)/g, '')
}

function buildGuide(modules, audience) {
  return Object.entries(modules)
    .map(([sourcePath, source]) => parseGuideChapter(source, sourcePath, audience))
    .sort((a, b) => a.order - b.order || a.title.localeCompare(b.title))
}

export const USER_GUIDE = buildGuide(userGuideModules, 'user')
export const ADMINISTRATIVE_GUIDE = buildGuide(administrativeGuideModules, 'admin')
