import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import {
  MCP_TOOL_CALL_PRICE_USD,
  PLATFORM_PRICE_USD,
  PRICING_FAQ,
  buildMarketingStructuredData,
  buildRobotsTxt,
  buildSitemapXml,
  buildStructuredData,
  getRouteMeta,
  normalizeSiteOrigin,
} from './config'
import { buildPublicRouteHtml } from './serverShell'

describe('SEO configuration', () => {
  it('ships the LawHand value proposition in the server-delivered HTML', () => {
    const html = readFileSync('index.html', 'utf8')

    expect(html).toContain('The whole matter, in hand.')
    expect(html).toContain('Book a demo')
    expect(html).not.toContain('<div id="root"></div>')
  })

  it('indexes only the substantiated public marketing and legal-policy routes', () => {
    expect(getRouteMeta('/').indexable).toBe(true)
    expect(getRouteMeta('/privacy/').canonicalPath).toBe('/privacy')
    expect(getRouteMeta('/terms').indexable).toBe(true)
    expect(getRouteMeta('/product/chat').indexable).toBe(true)
    expect(getRouteMeta('/product/mcp/').canonicalPath).toBe('/product/mcp')
    expect(getRouteMeta('/pricing').indexable).toBe(true)
    expect(getRouteMeta('/product').indexable).toBe(true)

    expect(getRouteMeta('/login').indexable).toBe(false)
    expect(getRouteMeta('/demo').title).toBe('Guided demo | LawHand')
    expect(getRouteMeta('/demo/session').title).toBe('Guided demo | LawHand')
    expect(getRouteMeta('/matters/customer-id').indexable).toBe(false)
    expect(getRouteMeta('/portal/client/matter?token=secret').indexable).toBe(false)
    expect(getRouteMeta('/unknown').indexable).toBe(false)
  })

  it('describes an unrecognized path as missing rather than as a private page', () => {
    // A stale inbound link should not be told it landed on a sign-in wall.
    expect(getRouteMeta('/no-such-page').title).toMatch(/Page not found/)
    expect(getRouteMeta('/no-such-page').canonicalPath).toBeNull()
    expect(getRouteMeta('/matters/abc').title).toMatch(/Matters/)
  })

  it('keeps sign-in-walled routes out of the crawl budget', () => {
    const robots = buildRobotsTxt('https://clarity.example')

    for (const route of ['/demo', '/login', '/signup', '/chat', '/matters', '/admin', '/platform', '/portal']) {
      expect(robots).toContain(`Disallow: ${route}/`)
    }
    // Public marketing routes must stay crawlable.
    expect(robots).not.toContain('Disallow: /pricing')
    expect(robots).not.toContain('Disallow: /product')
  })

  it('accepts only host-safe public origins', () => {
    expect(normalizeSiteOrigin('https://clarity.example/')).toBe('https://clarity.example')
    expect(normalizeSiteOrigin('http://localhost:3000')).toBe('http://localhost:3000')
    expect(normalizeSiteOrigin('')).toBe('')

    expect(() => normalizeSiteOrigin('http://clarity.example')).toThrow(/https/)
    expect(() => normalizeSiteOrigin('https://clarity.example/app')).toThrow(/bare origin/)
    expect(() => normalizeSiteOrigin('not-a-url')).toThrow(/absolute/)
  })

  it('emits crawl controls and a sitemap only when the production origin is known', () => {
    const withoutOrigin = buildRobotsTxt()
    expect(withoutOrigin).toContain('Disallow: /api/')
    expect(withoutOrigin).not.toContain('Sitemap:')

    const origin = 'https://clarity.example'
    const robots = buildRobotsTxt(origin)
    const sitemap = buildSitemapXml(origin)
    expect(robots).toContain(`Sitemap: ${origin}/sitemap.xml`)
    expect(sitemap).toContain(`<loc>${origin}/</loc>`)
    expect(sitemap).toContain(`<loc>${origin}/privacy</loc>`)
    expect(sitemap).toContain(`<loc>${origin}/terms</loc>`)
    expect(sitemap).toContain(`<loc>${origin}/product/chat</loc>`)
    expect(sitemap).toContain(`<loc>${origin}/product/mcp</loc>`)
    expect(sitemap).toContain(`<loc>${origin}/pricing</loc>`)
    expect(sitemap).toContain(`<loc>${origin}/product</loc>`)
    expect(sitemap).toContain('<lastmod>')
    expect(sitemap).not.toContain('/login')
    expect(sitemap).not.toContain('/matters')
  })

  it('limits structured data to claims supported by the product', () => {
    const graph = buildMarketingStructuredData('https://clarity.example')['@graph']
    expect(graph.map((node) => node['@type'])).toEqual([
      'Organization',
      'WebSite',
      'SoftwareApplication',
    ])
    const software = graph.find((node) => node['@type'] === 'SoftwareApplication')
    expect(software).toMatchObject({
      applicationCategory: 'BusinessApplication',
      operatingSystem: 'Modern web browser',
    })
    // The offer may state only the price the pricing page itself publishes,
    // and must not imply a self-serve purchase the product does not offer.
    expect(software.offers.price).toBe(PLATFORM_PRICE_USD)
    expect(software.offers.priceCurrency).toBe('USD')
    expect(software.offers).not.toHaveProperty('availability')

    // Ratings and reviews would be fabricated social proof; never emit them.
    for (const node of graph) {
      expect(node).not.toHaveProperty('aggregateRating')
      expect(node).not.toHaveProperty('review')
    }
  })

  it('gives every indexable route its own structured data', () => {
    const origin = 'https://clarity.example'

    for (const route of ['/', '/product', '/product/chat', '/product/mcp', '/pricing', '/privacy', '/terms']) {
      const types = buildStructuredData(origin, route)['@graph'].map((node) => node['@type'])
      expect(types).toContain('Organization')
      expect(types).toContain('WebSite')
    }

    expect(buildStructuredData(origin, '/pricing')['@graph'].map((node) => node['@type']))
      .toContain('BreadcrumbList')
    expect(buildStructuredData(origin, '/login')).toBeNull()
    expect(buildStructuredData(origin, '/no-such-page')).toBeNull()
    expect(buildStructuredData('', '/')).toBeNull()
  })

  it('publishes the pricing FAQ verbatim as FAQPage structured data', () => {
    const graph = buildStructuredData('https://clarity.example', '/pricing')['@graph']
    const faq = graph.find((node) => node['@type'] === 'FAQPage')

    expect(faq.mainEntity).toHaveLength(PRICING_FAQ.length)
    expect(faq.mainEntity[0].name).toBe(PRICING_FAQ[0][0])
    expect(faq.mainEntity[0].acceptedAnswer.text).toBe(PRICING_FAQ[0][1])
    // The published answers must carry the same prices as the rest of the site.
    expect(JSON.stringify(faq)).toContain(`$${PLATFORM_PRICE_USD}`)
    expect(JSON.stringify(faq)).toContain(`$${MCP_TOOL_CALL_PRICE_USD}`)
  })

  it.each([
    ['/privacy', 'Privacy Policy | LawHand', 'Privacy Policy', 'Terms of Use'],
    ['/terms', 'Terms of Use | LawHand', 'Terms of Use', 'Privacy Policy'],
  ])('builds substantive route-correct no-JavaScript HTML for %s', (route, title, heading, otherPolicy) => {
    const base = readFileSync('index.html', 'utf8')
    const html = buildPublicRouteHtml(base, route, 'https://clarity.example')

    expect(html).toContain(`<title>${title}</title>`)
    expect(html).toContain(`rel="canonical" href="https://clarity.example${route}"`)
    expect(html).toContain(`property="og:url" content="https://clarity.example${route}"`)
    expect(html).toContain(`<h1>${heading}</h1>`)
    expect(html).toContain('<article class="server-legal__article">')
    expect(html).toContain('<nav class="server-legal__contents" aria-label="On this page">')
    expect(html).toContain('<ol>')
    expect(html).toContain('<time datetime="2026-07-27">July 27, 2026</time>')
    expect(html.match(/<section id=/g)).toHaveLength(8)
    expect(html).toContain(`>${otherPolicy}</a>`)
    expect(html).toContain('mailto:matt@cybersafeadvisor.com')
    expect(html).not.toContain('The whole matter, in hand.')
    expect(html).toContain('<script type="module" src="/src/main.jsx"></script>')
  })

  it('keeps each legal shell specific to its policy', () => {
    const base = readFileSync('index.html', 'utf8')
    const privacy = buildPublicRouteHtml(base, '/privacy')
    const terms = buildPublicRouteHtml(base, '/terms')

    expect(privacy).toContain('How information is used')
    expect(privacy).toContain('Choices and privacy requests')
    expect(privacy).not.toContain('Acceptable use')
    expect(terms).toContain('Acceptable use')
    expect(terms).toContain('Disclaimers and liability')
    expect(terms).not.toContain('Choices and privacy requests')
  })

  it.each([
    ['/product/chat', 'Ask with the whole matter in hand.', 'matter-aware AI workspace'],
    ['/product/mcp', 'Bring LawHand context into the tools you already use.', '$0.45 per tool call'],
    ['/pricing', 'One clear platform price. Controlled expansion.', '$89 per user per month'],
    ['/product', 'One workspace for the whole matter.', 'Practice-area library'],
  ])('builds a substantive public product shell for %s', (route, heading, claim) => {
    const base = readFileSync('index.html', 'utf8')
    const html = buildPublicRouteHtml(base, route, 'https://lawhand.example')

    expect(html).toContain(`rel="canonical" href="https://lawhand.example${route}"`)
    expect(html).toContain(`<h1>${heading}</h1>`)
    expect(html).toContain(claim)
    expect(html).toContain('aria-label="LawHand product pages"')
    expect(html).toContain('mailto:matt@cybersafeadvisor.com')
  })
})
