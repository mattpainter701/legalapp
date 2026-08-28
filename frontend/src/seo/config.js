import { CORE_CAPABILITY_NAMES } from '../marketing/capabilities.js'

export const SITE_NAME = 'LawHand'

// The category sentence. A search engine, an AI answer engine, and a visitor
// skimming a result all need to learn what LawHand *is* before they learn what
// it is called, so every public surface leads with this and not the tagline.
export const SITE_CATEGORY = 'legal automation platform for law firms'
export const SITE_TAGLINE = 'The whole matter, in hand.'

// Kept under ~60 characters so Google renders it without truncation, and
// front-loaded with the product category rather than the brand slogan.
export const HOME_TITLE = 'LawHand | Legal Automation Platform for Law Firms'
// Kept under ~160 characters, and names the concrete functions a firm searches
// for. The home page must visibly contain every function named here.
export const HOME_DESCRIPTION =
  'LawHand unifies intake, matters, conflict review, documents, client action, billing, and source-linked AI legal research in one review-first legal workspace.'

export const ORGANIZATION_DESCRIPTION =
  'LawHand builds a legal automation platform that connects client and matter CRM, intake, conflict review, tasks and deadlines, document preparation, client portal and signature workflows, time and invoicing, practice-area skills, and source-linked AI legal research in one tenant-isolated workspace.'

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
export const PUBLIC_CONTENT_LASTMOD = '2026-08-28'

export const PRICING_FAQ = Object.freeze([
  Object.freeze([
    `What is included in the $${PLATFORM_PRICE_USD} seat?`,
    'The LawHand platform seat covers the firm workspace and its licensed modules. Enabled integrations, onboarding scope, premium model usage, and specialized service commitments are confirmed in your order.',
  ]),
  Object.freeze([
    'How is Research MCP offered?',
    `LawHand Research MCP is offered as a controlled pilot to approved LawHand customers with Research billing enabled. It is research-only, with no workspace or matter access, and the pilot price is $${MCP_TOOL_CALL_PRICE_USD} per successful tool call. Coverage depends on the configured public-authority corpus.`,
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

/**
 * Home-page questions, published verbatim as FAQPage structured data.
 *
 * These answer the "what is this product" question a search result cannot fit,
 * and they are the copy an AI answer engine is most likely to quote, so every
 * answer must stay inside what README.md substantiates and must state a gate
 * where one exists.
 */
export const HOME_FAQ = Object.freeze([
  Object.freeze([
    'What is LawHand?',
    `LawHand is a ${SITE_CATEGORY}. It combines client and matter CRM, caller intake, tasks and deadlines, document preparation, time tracking and invoicing, practice-area workflows, and source-linked AI legal research in one tenant-isolated workspace.`,
  ]),
  Object.freeze([
    'Does LawHand include CRM and billing, or only AI?',
    'Both. LawHand runs the firm\u2019s day-to-day record \u2014 matters, contacts, parties, notes, assignments, budgets, and timelines \u2014 alongside time and expense capture, invoices, payments, retainers, LEDES export, and optional Stripe payment flows. AI-assisted research and drafting sit on top of that record rather than replacing it.',
  ]),
  Object.freeze([
    'Can LawHand prepare and automate documents?',
    'Yes. LawHand analyzes DOCX and TXT templates and substitutes matter variables, and it retains PDF sources with AcroForm field discovery, reviewed field mapping, a binary review preview, flattened output by default, and integrity checks before the finished file is stored on the matter.',
  ]),
  Object.freeze([
    'What is LawHand Research MCP?',
    `LawHand Research MCP is a controlled-pilot Model Context Protocol server that lets an approved assistant such as ChatGPT or Claude, or an API client, retrieve configured public legal authority through a scoped credential. It is research-only and cannot reach LawHand matters, contacts, tasks, documents, templates, or firm configuration. Approved pilot customers pay $${MCP_TOOL_CALL_PRICE_USD} per successful tool call.`,
  ]),
  Object.freeze([
    'Can an AI assistant reach our matters through MCP?',
    'Only through Workspace MCP, which is a separate, release-gated surface. It uses OAuth 2.1 authorization code with PKCE, carries a real tenant, user, scope set, and expiry, and issues a revocable token family, so access stays inside what the firm authorized for that person.',
  ]),
  Object.freeze([
    'Who is LawHand for?',
    'Law firms and legal teams that want intake, matters, documents, deadlines, billing, and research in one system rather than in separate tools. A firm can begin with a focused caller-intake and task workflow and add the broader platform later.',
  ]),
  Object.freeze([
    'Is AI-assisted output safe to rely on?',
    'It is presented for attorney review. Every tagged claim in a LawHand answer is labeled cited, verify, or model, and source links point back to the original authority or firm document. These are review aids, not a substitute for professional judgment, and no citation label guarantees that an authority remains good law.',
  ]),
  Object.freeze([
    'Is our firm data isolated from other firms?',
    'Yes. Firm workspaces are tenant-isolated and enforced in both navigation and API middleware. Storage encryption and model-provider data handling additionally depend on the infrastructure, provider, and tenant policy configured for your deployment.',
  ]),
])

/**
 * The public pages LawHand wants Google to consider for sitelinks, published
 * as SiteNavigationElement structured data and rendered as real links in both
 * the app header and the no-JavaScript shells. Structured data alone does not
 * earn sitelinks; consistent internal linking to the same short list does.
 */
export const PRIMARY_NAVIGATION = Object.freeze([
  Object.freeze({ path: '/product', label: 'Platform', shortLabel: 'Platform' }),
  Object.freeze({ path: '/product/chat', label: 'AI Chat', shortLabel: 'AI Chat' }),
  Object.freeze({ path: '/product/mcp', label: 'Legal Research MCP', shortLabel: 'MCP' }),
  Object.freeze({ path: '/pricing', label: 'Pricing', shortLabel: 'Pricing' }),
  Object.freeze({ path: '/request-demo', label: 'Book a Demo', shortLabel: 'Book demo' }),
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
    title: 'Legal Automation Platform | LawHand Product Tour',
    description:
      'Follow a legal matter from intake and conflict review through tasks, documents, attorney review, client action, signature, billing, and connected systems in LawHand.',
    canonicalPath: '/product',
    indexable: true,
    priority: '0.9',
    breadcrumb: 'Platform',
  },
  '/product/chat': {
    title: 'Matter-Aware AI Chat for Legal Teams | LawHand',
    description:
      'Research, review, summarize, and draft with LawHand AI chat connected to the active matter and the sources your firm authorizes, with every claim labeled cited, verify, or model.',
    canonicalPath: '/product/chat',
    indexable: true,
    priority: '0.8',
    breadcrumb: 'AI Chat',
    parentPath: '/product',
  },
  '/product/mcp': {
    title: 'Legal Research MCP Server for AI Assistants | LawHand',
    description:
      'Evaluate controlled-pilot retrieval of configured public legal authority in approved assistants, or connect approved assistants to a tenant-scoped LawHand workspace over scoped OAuth.',
    canonicalPath: '/product/mcp',
    indexable: true,
    priority: '0.8',
    breadcrumb: 'MCP',
    parentPath: '/product',
  },
  '/request-demo': {
    title: 'Book a LawHand Demo | Legal Automation Platform',
    description:
      'Request a focused LawHand demo built around your firm’s intake, matters, documents, billing, and review controls.',
    canonicalPath: '/request-demo',
    indexable: true,
    priority: '0.7',
    breadcrumb: 'Book a Demo',
  },
  '/pricing': {
    title: 'Pricing | LawHand Legal Automation Platform',
    description:
      `LawHand is $${PLATFORM_PRICE_USD} per user per month billed annually. Research MCP is a controlled pilot at $${MCP_TOOL_CALL_PRICE_USD} per successful tool call for approved customers.`,
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
  // Catch-all parent. Every portal URL is token-gated, so the crawl rule must
  // cover the whole subtree rather than only the four routes shipped today.
  // The specific entries above still win in getRouteMeta, which matches in
  // order, so each portal keeps its own title.
  ['/portal', 'Secure portal'],
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
  // Rules are prefix matches, so `/login` covers both `/login` and
  // `/login/anything`. Writing `/login/` would have left the sign-in page
  // itself crawlable, which is exactly the URL that must stay out of results.
  const disallows = ['/api/', ...workspaceCrawlDisallows()]
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
      // Omit the hint entirely rather than publishing `undefined` when a route
      // does not declare one.
      ...(route.priority ? [`    <priority>${route.priority}</priority>`] : []),
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

/**
 * Normalize the optional organization identity a deployment supplies.
 *
 * `config.js` is imported both by the browser bundle and by `vite.config.js`
 * running in Node, so it must never read `import.meta.env` itself. Each caller
 * passes what it knows instead.
 */
export function normalizeOrganizationProfile(profile = {}) {
  const contactUrl = String(profile.contactUrl || '').trim()
  const email = contactUrl.startsWith('mailto:')
    ? contactUrl.slice('mailto:'.length).split('?')[0]
    : ''
  const telephone = String(profile.telephone || '').trim()

  // Comma-separated authoritative profile URLs: the Google Business Profile
  // short link, LinkedIn, and similar. `sameAs` is how Google reconciles those
  // profiles with this site into one entity, so a wrong URL is worse than none.
  const sameAs = (Array.isArray(profile.sameAs) ? profile.sameAs : String(profile.sameAs || '').split(','))
    .map((value) => String(value).trim())
    .filter(Boolean)
    .filter((value) => {
      try {
        return ['http:', 'https:'].includes(new URL(value).protocol)
      } catch {
        return false
      }
    })

  return { email, telephone, sameAs }
}

function organizationNode(siteOrigin, profile) {
  const { email, telephone, sameAs } = normalizeOrganizationProfile(profile)
  const contactPoint = (email || telephone)
    ? {
      '@type': 'ContactPoint',
      contactType: 'sales',
      areaServed: 'US',
      availableLanguage: 'English',
      ...(email ? { email } : {}),
      ...(telephone ? { telephone } : {}),
    }
    : null

  return {
    '@type': 'Organization',
    '@id': `${siteOrigin}/#organization`,
    name: SITE_NAME,
    url: `${siteOrigin}/`,
    description: ORGANIZATION_DESCRIPTION,
    slogan: SITE_TAGLINE,
    logo: {
      '@type': 'ImageObject',
      url: `${siteOrigin}/icons/icon-512x512.png`,
      width: 512,
      height: 512,
    },
    image: `${siteOrigin}/social-card-v2.png`,
    ...(sameAs.length ? { sameAs } : {}),
    ...(contactPoint ? { contactPoint } : {}),
  }
}

/**
 * The short public menu, restated for search engines. This is a hint about
 * which pages deserve sitelinks; the header and footer links are the evidence.
 */
function siteNavigationNode(siteOrigin) {
  return {
    '@type': 'ItemList',
    '@id': `${siteOrigin}/#navigation`,
    name: `${SITE_NAME} site navigation`,
    itemListElement: PRIMARY_NAVIGATION.map(({ path, label }, index) => ({
      '@type': 'SiteNavigationElement',
      position: index + 1,
      name: label,
      description: PUBLIC_ROUTE_META[path]?.description,
      url: `${siteOrigin}${PUBLIC_ROUTE_META[path]?.canonicalPath || path}`,
    })),
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
    applicationSubCategory: 'Legal automation and practice management software',
    operatingSystem: 'Modern web browser',
    // Every entry is also rendered as visible copy on the home page; structured
    // data must never claim a capability the page does not show.
    featureList: CORE_CAPABILITY_NAMES,
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

function faqNode(siteOrigin, pathname, entries) {
  const base = pathname === '/' ? `${siteOrigin}/` : `${siteOrigin}${pathname}`
  return {
    '@type': 'FAQPage',
    '@id': `${base}#faq`,
    mainEntity: entries.map(([question, answer]) => ({
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
export function buildStructuredData(siteOrigin, pathname = '/', profile = {}) {
  if (!siteOrigin) return null
  const path = normalizePathname(pathname)
  const route = PUBLIC_ROUTE_META[path]
  if (!route?.indexable) return null

  const graph = [organizationNode(siteOrigin, profile), websiteNode(siteOrigin)]

  if (path === '/' || path === '/pricing' || path === '/product') {
    graph.push(softwareApplicationNode(siteOrigin))
  }

  // Sitelink candidates are declared once, on the entry point Google is most
  // likely to expand.
  if (path === '/') graph.push(siteNavigationNode(siteOrigin))

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

  if (path === '/') graph.push(faqNode(siteOrigin, '/', HOME_FAQ))
  if (path === '/pricing') graph.push(faqNode(siteOrigin, '/pricing', PRICING_FAQ))

  return { '@context': 'https://schema.org', '@graph': graph }
}

/** Retained for the home-page shell; delegates to the route-aware builder. */
export function buildMarketingStructuredData(siteOrigin, profile = {}) {
  return buildStructuredData(siteOrigin, '/', profile)
}
