import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import {
  buildMarketingStructuredData,
  buildRobotsTxt,
  buildSitemapXml,
  getRouteMeta,
  normalizeSiteOrigin,
} from './config'

describe('SEO configuration', () => {
  it('ships useful Call Intake content in the server-delivered HTML', () => {
    const html = readFileSync('index.html', 'utf8')

    expect(html).toContain('Start focused with a dependable intake workflow.')
    expect(html).toContain('Request a Call Intake workspace')
    expect(html).not.toContain('<div id="root"></div>')
  })

  it('indexes only the substantiated public marketing and legal-summary routes', () => {
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
})
