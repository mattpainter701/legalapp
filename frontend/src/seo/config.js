export const SITE_NAME = 'LawHand'

export const HOME_TITLE = 'LawHand | The Whole Matter, in Hand'
export const HOME_DESCRIPTION =
  'LawHand connects every fact, deadline, document, and decision in one living matter record for modern legal teams.'

export const PRIVATE_DESCRIPTION =
  'Sign in to the private LawHand workspace for your firm.'

export const NOT_FOUND_TITLE = `Page not found | ${SITE_NAME}`
export const NOT_FOUND_DESCRIPTION =
  'This LawHand page does not exist. Return to the LawHand home page, product pages, or pricing.'

// Single source of truth for every published price. The marketing pages, the
// route descriptions, and the structured data all read these, so a price can
// never be corrected in one place and left stale in another.
export const PLATFORM_PRICE_USD = '89'
export const MCP_TOOL_CALL_PRICE_USD = '0.45'

// Bumped whenever public marketing copy changes; feeds sitemap <lastmod>.
export const PUBLIC_CONTENT_LASTMOD = '2026-08-15'

export const PRICING_FAQ = Object.freeze([
  Object.freeze([
    `What is included in the $${PLATFORM_PRICE_USD} seat?`,
    'The LawHand platform seat covers the firm workspace and its licensed modules. Enabled integrations, onboarding scope, premium model usage, and specialized service commitments are confirmed in your order.',
  ]),
  Object.freeze([
    'Is MCP generally available?',
    `Not yet. LawHand Research MCP is in private preview while its public product, billing, monitoring, and recovery gates are completed. It is research-only, with no workspace or matter access. The intended public price is $${MCP_TOOL_CALL_PRICE_USD} per tool call.`,
  ]),
  Object.freeze([
    'Do administrators count as licensed users?',
    'Seat scope and directory treatment are documented during onboarding so the licensed population matches the firm’s approved rollout.',
  ]),
  Object.freeze([
    'Can a firm begin with only intake?',
    'Yes. Call Intake can be deployed as a focused first workflow, with the broader platform added when the firm is ready.',
  ]),
  Object.freeze([
    'How does LawHand handle AI-assisted output?',
    'AI-assisted work is presented for attorney review. Source links and confidence cues support verification; they are review aids, not a substitute for professional judgment.',
  ]),
  Object.freeze([
    'Is our firm data isolated from other firms?',
    'Yes. Firm workspaces are tenant-isolated. Storage encryption and model-provider data handling additionally depend on the infrastructure, provider, and tenant policy configured for your deployment.',
  ]),
])

export const PUBLIC_ROUTE_META = Object.freeze({
  '/': {
    title: HOME_TITLE,
    description: HOME_DESCRIPTION,
    canonicalPath: '/',
    indexable: true,
    priority: '1.0',
  },
  '/product': {
    title: 'The LawHand Platform for Law Firm Operations',
    description:
      'See how the LawHand platform connects intake, matters, documents, deadlines, billing, practice-area skills, matter-aware AI chat, and controlled MCP integrations.',
    canonicalPath: '/product',
    indexable: true,
    priority: '0.9',
    breadcrumb: 'Platform',
  },
  '/product/chat': {
    title: 'Matter-Aware AI Chat for Legal Teams | LawHand',
    description:
      'Research, review, summarize, and draft with LawHand AI chat connected to the active matter and the sources your firm authorizes.',
    canonicalPath: '/product/chat',
    indexable: true,
    priority: '0.8',
    breadcrumb: 'AI Chat',
    parentPath: '/product',
  },
  '/product/mcp': {
    title: 'LawHand Research MCP for Public Legal Authority',
    description:
      'LawHand Research MCP connects ChatGPT, Claude, and API clients to approved public legal authority through OAuth or a scoped API token, with PAYG metering and no workspace or matter access.',
    canonicalPath: '/product/mcp',
    indexable: true,
    priority: '0.8',
    breadcrumb: 'MCP',
    parentPath: '/product',
  },
  '/request-demo': {
    title: 'Book a LawHand Demo',
    description: 'Request a focused LawHand demo built around your firm’s workflows, sources, and review controls.',
    canonicalPath: '/demo',
    indexable: true,
  },
  '/pricing': {    title: 'Pricing | LawHand',
    description:
      `LawHand is $${PLATFORM_PRICE_USD} per user per month billed annually. LawHand MCP is in private preview at an intended public price of $${MCP_TOOL_CALL_PRICE_USD} per tool call.`,
    canonicalPath: '/pricing',
    indexable: true,
    priority: '0.9',
    breadcrumb: 'Pricing',
  },
  '/privacy': {
    title: 'Privacy Policy | LawHand',
    description:
      'Read the LawHand Privacy Policy, including how account and workspace data is collected, used, shared, retained, and protected.',
    canonicalPath: '/privacy',
    indexable: true,
    priority: '0.3',
    breadcrumb: 'Privacy Policy',
  },
  '/terms': {
    title: 'Terms of Use | LawHand',
    description:
      'Read the LawHand Terms of Use, including service responsibilities, acceptable use, AI-assisted features, and account administration.',
    canonicalPath: '/terms',
    indexable: true,
    priority: '0.3',
    breadcrumb: 'Terms of Use',
  },
})

const WORKSPACE_ROUTE_TITLES = [
  ['/demo', 'Guided demo'],
  ['/forgot-password', 'Reset password'],
  ['/reset-password', 'Reset password'],
  ['/auth/callback', 'Completing sign in'],
  ['/portal/client/matter', 'Secure client portal'],
  ['/portal/client/accept', 'Secure client portal'],
  ['/portal/case', 'Secure case portal'],
  ['/portal/accept', 'Secure case portal'],
  ['/plugins/commercial/renewals', 'Renewal tracker'],
  ['/plugins/trust-estate/estates', 'Trust and estate matters'],
  ['/plugins/domestic/cases', 'Domestic relations matters'],
  ['/plugins/mediation/cases', 'Mediation matters'],
  ['/intake/dashboard', 'Intake dashboard'],
  ['/time-tracking', 'Time tracking'],
  ['/teams/config', 'Teams configuration'],
  ['/login', 'Sign in'],
  ['/signup', 'Request access'],
  ['/chat', 'Legal workspace'],
  ['/matters', 'Matters'],
  ['/calendar', 'Calendar'],
  ['/teams', 'Microsoft Teams'],
  ['/communications', 'Communications'],
  ['/invoices', 'Invoices'],
  ['/reports', 'Reports'],
  ['/trust', 'Trust accounting'],
  ['/templates', 'Document automation'],
  ['/billing', 'Billing'],
  ['/contacts', 'Contacts'],
  ['/tasks', 'Tasks'],
  ['/intake', 'Caller intake'],
  ['/plugins', 'Add-on modules'],
  ['/profile', 'Profile'],
  ['/guide', 'User guide'],
  ['/admin', 'Administration'],
  ['/onboarding', 'Onboarding'],
  ['/platform', 'Platform administration'],
]

export function normalizePathname(pathname) {
  const path = typeof pathname === 'string' && pathname.startsWith('/') ? pathname : '/'
  if (path === '/') return path
  return path.replace(/\/+$/, '') || '/'
}

/** True when the path resolves to a real route rather than the 404 handler. */
export function isKnownRoute(pathname) {
  const path = normalizePathname(pathname)
  if (Object.hasOwn(PUBLIC_ROUTE_META, path)) return true
  return WORKSPACE_ROUTE_TITLES.some(([prefix]) => (
    path === prefix || path.startsWith(`${prefix}/`)
  ))
}

export function getRouteMeta(pathname) {
  const path = normalizePathname(pathname)
  const publicMeta = PUBLIC_ROUTE_META[path]
  if (publicMeta) return publicMeta

  const match = WORKSPACE_ROUTE_TITLES.find(([prefix]) => (
    path === prefix || path.startsWith(`${prefix}/`)
  ))

  // An unrecognized path renders the 404 view. Give it its own non-indexable
  // metadata instead of describing it as a private workspace page.
  if (!match) {
    return {
      title: NOT_FOUND_TITLE,
      description: NOT_FOUND_DESCRIPTION,
      canonicalPath: null,
      indexable: false,
    }
  }

  return {
    title: `${match[1]} | ${SITE_NAME}`,
    description: PRIVATE_DESCRIPTION,
    canonicalPath: null,
    indexable: false,
  }
}

export function normalizeSiteOrigin(value) {
  if (!value || !String(value).trim()) return ''

  let url
  try {
    url = new URL(String(value).trim())
  } catch {
    throw new Error('VITE_PUBLIC_SITE_URL must be an absolute http(s) URL.')
  }

  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('VITE_PUBLIC_SITE_URL must use http or https.')
  }
  const localHost = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
  if (url.protocol !== 'https:' && !localHost) {
    throw new Error('VITE_PUBLIC_SITE_URL must use https outside local development.')
  }
  if (url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) {
    throw new Error('VITE_PUBLIC_SITE_URL must be a bare origin without credentials, path, query, or fragment.')
  }

  return url.origin
}

/**
 * Sign-in-walled routes never belong in search results. The prerendered SPA
 * shell that nginx serves for them still carries the indexable home metadata
 * until React replaces it, so a crawler that does not execute JavaScript would
 * otherwise treat every workspace URL as a duplicate of the home page.
 */
export function workspaceCrawlDisallows() {
  const prefixes = WORKSPACE_ROUTE_TITLES
    .map(([prefix]) => prefix)
    .sort((a, b) => a.length - b.length)

  const roots = []
  for (const prefix of prefixes) {
    if (roots.some((root) => prefix === root || prefix.startsWith(`${root}/`))) continue
    roots.push(prefix)
  }
  return roots.sort()
}

export function buildRobotsTxt(siteOrigin = '') {
  const disallows = ['/api/', ...workspaceCrawlDisallows().map((route) => `${route}/`)]
  const sitemap = siteOrigin ? `\nSitemap: ${siteOrigin}/sitemap.xml` : ''
  return [
    'User-agent: *',
    'Allow: /',
    ...disallows.map((route) => `Disallow: ${route}`),
    sitemap,
    '',
  ].filter((line, index, lines) => line || index === lines.length - 1).join('\n')
}

export function buildSitemapXml(siteOrigin) {
  if (!siteOrigin) return ''
  const urls = Object.values(PUBLIC_ROUTE_META)
    .filter((route) => route.indexable)
    .map((route) => [
      '  <url>',
      `    <loc>${siteOrigin}${route.canonicalPath}</loc>`,
      `    <lastmod>${PUBLIC_CONTENT_LASTMOD}</lastmod>`,
      `    <priority>${route.priority}</priority>`,
      '  </url>',
    ].join('\n'))
    .join('\n')

  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    urls,
    '</urlset>',
    '',
  ].join('\n')
}

function organizationNode(siteOrigin) {
  return {
    '@type': 'Organization',
    '@id': `${siteOrigin}/#organization`,
    name: SITE_NAME,
    url: `${siteOrigin}/`,
    logo: {
      '@type': 'ImageObject',
      url: `${siteOrigin}/icons/icon-512x512.png`,
      width: 512,
      height: 512,
    },
  }
}

function websiteNode(siteOrigin) {
  return {
    '@type': 'WebSite',
    '@id': `${siteOrigin}/#website`,
    name: SITE_NAME,
    url: `${siteOrigin}/`,
    inLanguage: 'en-US',
    publisher: { '@id': `${siteOrigin}/#organization` },
  }
}

function softwareApplicationNode(siteOrigin) {
  return {
    '@type': 'SoftwareApplication',
    '@id': `${siteOrigin}/#software`,
    name: SITE_NAME,
    url: `${siteOrigin}/`,
    description: HOME_DESCRIPTION,
    applicationCategory: 'BusinessApplication',
    operatingSystem: 'Modern web browser',
    audience: {
      '@type': 'Audience',
      audienceType: 'Law firms and legal professionals',
    },
    offers: {
      '@type': 'Offer',
      url: `${siteOrigin}/pricing`,
      price: PLATFORM_PRICE_USD,
      priceCurrency: 'USD',
      category: 'Subscription',
      priceSpecification: {
        '@type': 'UnitPriceSpecification',
        price: PLATFORM_PRICE_USD,
        priceCurrency: 'USD',
        unitText: 'user',
        referenceQuantity: {
          '@type': 'QuantitativeValue',
          value: 1,
          unitCode: 'MON',
        },
      },
    },
    publisher: { '@id': `${siteOrigin}/#organization` },
    isPartOf: { '@id': `${siteOrigin}/#website` },
  }
}

function breadcrumbNode(siteOrigin, pathname) {
  const route = PUBLIC_ROUTE_META[pathname]
  if (!route?.breadcrumb) return null

  const trail = []
  let cursor = route
  while (cursor) {
    trail.unshift(cursor)
    cursor = cursor.parentPath ? PUBLIC_ROUTE_META[cursor.parentPath] : null
  }

  return {
    '@type': 'BreadcrumbList',
    '@id': `${siteOrigin}${pathname}#breadcrumb`,
    itemListElement: [
      {
        '@type': 'ListItem',
        position: 1,
        name: 'Home',
        item: `${siteOrigin}/`,
      },
      ...trail.map((entry, index) => ({
        '@type': 'ListItem',
        position: index + 2,
        name: entry.breadcrumb,
        item: `${siteOrigin}${entry.canonicalPath}`,
      })),
    ],
  }
}

function faqNode(siteOrigin) {
  return {
    '@type': 'FAQPage',
    '@id': `${siteOrigin}/pricing#faq`,
    mainEntity: PRICING_FAQ.map(([question, answer]) => ({
      '@type': 'Question',
      name: question,
      acceptedAnswer: { '@type': 'Answer', text: answer },
    })),
  }
}

/**
 * Structured data for a public route. Every indexable page carries the
 * organization and website identity so search engines can consolidate the
 * entity; individual pages add the type that describes them.
 */
export function buildStructuredData(siteOrigin, pathname = '/') {
  if (!siteOrigin) return null
  const path = normalizePathname(pathname)
  const route = PUBLIC_ROUTE_META[path]
  if (!route?.indexable) return null

  const graph = [organizationNode(siteOrigin), websiteNode(siteOrigin)]

  if (path === '/' || path === '/pricing' || path === '/product') {
    graph.push(softwareApplicationNode(siteOrigin))
  }

  if (path !== '/') {
    graph.push({
      '@type': 'WebPage',
      '@id': `${siteOrigin}${path}#webpage`,
      url: `${siteOrigin}${path}`,
      name: route.title,
      description: route.description,
      inLanguage: 'en-US',
      isPartOf: { '@id': `${siteOrigin}/#website` },
      about: { '@id': `${siteOrigin}/#software` },
    })
  }

  const breadcrumb = breadcrumbNode(siteOrigin, path)
  if (breadcrumb) graph.push(breadcrumb)

  if (path === '/pricing') graph.push(faqNode(siteOrigin))

  return { '@context': 'https://schema.org', '@graph': graph }
}

/** Retained for the home-page shell; delegates to the route-aware builder. */
export function buildMarketingStructuredData(siteOrigin) {
  return buildStructuredData(siteOrigin, '/')
}
