import { getRouteMeta } from './config.js'

const LEGAL_SHELLS = Object.freeze({
  '/privacy': {
    heading: 'Privacy summary',
    lead:
      'Clarity Legal processes account and workspace data to provide the service. Firm data is isolated by tenant. Model-provider data handling depends on the provider and tenant configuration selected by your organization. Your firm administrator controls connected services and available retention settings.',
  },
  '/terms': {
    heading: 'Service summary',
    lead:
      'This page is a product summary, not a contract. Use of Clarity Legal is governed by the subscription agreement provided to your organization. The service assists legal professionals but does not replace professional judgment, source verification, or your firm\u2019s compliance obligations.',
  },
})

const SHARED_NOTICE =
  'The controlling subscription terms and, where applicable, data-processing agreement are provided by your organization. Contact your firm administrator for those documents, the applicable retention policy, subprocessors, and workspace-specific privacy terms.'

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function replaceMeta(html, attribute, key, content) {
  const escaped = escapeHtml(content)
  const pattern = new RegExp(
    `<meta\\s+([^>]*${attribute}=["']${key}["'][^>]*)>`,
    'i',
  )
  return html.replace(pattern, (tag) => {
    if (/content=["'][^"']*["']/i.test(tag)) {
      return tag.replace(/content=["'][^"']*["']/i, `content="${escaped}"`)
    }
    return tag.replace(/\s*\/?\s*>$/, ` content="${escaped}" />`)
  })
}

function replaceRootContents(html, contents) {
  const marker = '<div id="root">'
  const rootStart = html.indexOf(marker)
  const bodyEnd = html.indexOf('</body>', rootStart)
  const rootEnd = html.lastIndexOf('</div>', bodyEnd)
  if (rootStart < 0 || bodyEnd < 0 || rootEnd < rootStart) {
    throw new Error('Built frontend HTML does not contain the expected React root shell.')
  }
  return `${html.slice(0, rootStart + marker.length)}\n${contents}\n    ${html.slice(rootEnd)}`
}

function legalShellMarkup(pathname) {
  const route = LEGAL_SHELLS[pathname]
  return `      <main class="server-legal">
        <article class="server-legal__card">
          <a class="server-legal__brand" href="/">Clarity Legal</a>
          <h1>${escapeHtml(route.heading)}</h1>
          <p>${escapeHtml(route.lead)}</p>
          <p>${escapeHtml(SHARED_NOTICE)}</p>
        </article>
      </main>`
}

/** Derive a crawl-correct, no-JavaScript shell from Vite's final SPA index. */
export function buildPublicRouteHtml(baseHtml, pathname, siteOrigin = '') {
  if (!Object.hasOwn(LEGAL_SHELLS, pathname)) {
    throw new Error(`No public server shell is defined for ${pathname}`)
  }
  const meta = getRouteMeta(pathname)
  const canonical = siteOrigin ? `${siteOrigin}${pathname}` : pathname
  let html = baseHtml
    .replace(/<title>[\s\S]*?<\/title>/i, `<title>${escapeHtml(meta.title)}</title>`)
    .replace(
      /<link\s+rel=["']canonical["'][^>]*>/i,
      `<link rel="canonical" href="${escapeHtml(canonical)}" />`,
    )
    .replace(
      /<script[^>]*data-seo-structured-data[^>]*>[\s\S]*?<\/script>\s*/gi,
      '',
    )

  html = replaceMeta(html, 'name', 'description', meta.description)
  html = replaceMeta(html, 'name', 'robots', 'index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1')
  html = replaceMeta(html, 'name', 'googlebot', 'index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1')
  html = replaceMeta(html, 'property', 'og:title', meta.title)
  html = replaceMeta(html, 'property', 'og:description', meta.description)
  html = replaceMeta(html, 'property', 'og:url', canonical)
  html = replaceMeta(html, 'name', 'twitter:title', meta.title)
  html = replaceMeta(html, 'name', 'twitter:description', meta.description)

  return replaceRootContents(html, legalShellMarkup(pathname))
}

export const PUBLIC_SERVER_SHELL_PATHS = Object.freeze(Object.keys(LEGAL_SHELLS))
