import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import {
  buildMarketingStructuredData,
  buildRobotsTxt,
  buildSitemapXml,
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

    expect(getRouteMeta('/login').indexable).toBe(false)
    expect(getRouteMeta('/matters/customer-id').indexable).toBe(false)
    expect(getRouteMeta('/portal/client/matter?token=secret').indexable).toBe(false)
    expect(getRouteMeta('/unknown').indexable).toBe(false)
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
    expect(graph.find((node) => node['@type'] === 'SoftwareApplication')).toMatchObject({
      applicationCategory: 'BusinessApplication',
      operatingSystem: 'Modern web browser',
    })
    for (const node of graph) {
      expect(node).not.toHaveProperty('aggregateRating')
      expect(node).not.toHaveProperty('review')
      expect(node).not.toHaveProperty('offers')
    }
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
    expect(html).toContain('mailto:contact@perevagagroup.com')
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
})
