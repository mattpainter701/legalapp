export const SITE_NAME = 'Clarity Legal'

export const HOME_TITLE = 'Clarity Legal | Law Firm Operations & Legal AI'
export const HOME_DESCRIPTION =
  'Clarity Legal helps law firms manage intake, matters, tasks, documents, billing, and source-aware AI-assisted work, with attorney review.'

export const PRIVATE_DESCRIPTION =
  'Sign in to the private Clarity Legal workspace for your firm.'

export const PUBLIC_ROUTE_META = Object.freeze({
  '/': {
    title: HOME_TITLE,
    description: HOME_DESCRIPTION,
    canonicalPath: '/',
    indexable: true,
  },
  '/privacy': {
    title: 'Privacy Summary | Clarity Legal',
    description:
      'Read how Clarity Legal handles account and workspace data, tenant isolation, connected services, and provider-specific data processing.',
    canonicalPath: '/privacy',
    indexable: true,
  },
  '/terms': {
    title: 'Service Summary | Clarity Legal',
    description:
      'Read the Clarity Legal service summary and important guidance for professional judgment, source verification, and firm compliance.',
    canonicalPath: '/terms',
    indexable: true,
  },
})

const WORKSPACE_ROUTE_TITLES = [
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
  ['/admin', 'Administration'],
  ['/onboarding', 'Onboarding'],
  ['/platform', 'Platform administration'],
]

export function normalizePathname(pathname) {
  const path = typeof pathname === 'string' && pathname.startsWith('/') ? pathname : '/'
  if (path === '/') return path
  return path.replace(/\/+$/, '') || '/'
}

export function getRouteMeta(pathname) {
  const path = normalizePathname(pathname)
  const publicMeta = PUBLIC_ROUTE_META[path]
  if (publicMeta) return publicMeta

  const match = WORKSPACE_ROUTE_TITLES.find(([prefix]) => (
    path === prefix || path.startsWith(`${prefix}/`)
  ))

  return {
    title: `${match?.[1] || 'Secure workspace'} | ${SITE_NAME}`,
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

export function buildRobotsTxt(siteOrigin = '') {
  const sitemap = siteOrigin ? `\nSitemap: ${siteOrigin}/sitemap.xml` : ''
  return [
    'User-agent: *',
    'Allow: /',
    'Disallow: /api/',
    'Disallow: /auth/callback',
    'Disallow: /portal/',
    sitemap,
    '',
  ].filter((line, index, lines) => line || index === lines.length - 1).join('\n')
}

export function buildSitemapXml(siteOrigin) {
  if (!siteOrigin) return ''
  const urls = Object.values(PUBLIC_ROUTE_META)
    .filter((route) => route.indexable)
    .map((route) => `  <url><loc>${siteOrigin}${route.canonicalPath}</loc></url>`)
    .join('\n')

  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    urls,
    '</urlset>',
    '',
  ].join('\n')
}

export function buildMarketingStructuredData(siteOrigin) {
  if (!siteOrigin) return null
  const organizationId = `${siteOrigin}/#organization`
  const websiteId = `${siteOrigin}/#website`

  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'Organization',
        '@id': organizationId,
        name: SITE_NAME,
        url: `${siteOrigin}/`,
        logo: {
          '@type': 'ImageObject',
          url: `${siteOrigin}/icons/icon-512x512.png`,
          width: 512,
          height: 512,
        },
      },
      {
        '@type': 'WebSite',
        '@id': websiteId,
        name: SITE_NAME,
        url: `${siteOrigin}/`,
        inLanguage: 'en-US',
        publisher: { '@id': organizationId },
      },
      {
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
        publisher: { '@id': organizationId },
        isPartOf: { '@id': websiteId },
      },
    ],
  }
}
