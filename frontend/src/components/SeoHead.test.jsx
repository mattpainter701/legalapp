import { render, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SeoHead from './SeoHead'
import { HOME_DESCRIPTION, HOME_TITLE } from '../seo/config'

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SeoHead />
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.unstubAllEnvs()
  document.head.querySelector('script[data-seo-structured-data]')?.remove()
  document.head.querySelector('link[rel="canonical"]')?.remove()
})

describe('SeoHead', () => {
  it('publishes canonical marketing metadata and truthful structured data', async () => {
    vi.stubEnv('VITE_PUBLIC_SITE_URL', 'https://clarity.example')
    renderAt('/?campaign=ignored')

    await waitFor(() => expect(document.title).toBe(HOME_TITLE))
    // The result a firm sees in Google must say what LawHand is.
    expect(document.title).toContain('Legal Automation Platform')
    expect(document.querySelector('meta[name="description"]'))
      .toHaveAttribute('content', HOME_DESCRIPTION)
    expect(document.querySelector('meta[name="robots"]')).toHaveAttribute('content', expect.stringContaining('index, follow'))
    expect(document.querySelector('link[rel="canonical"]')).toHaveAttribute('href', 'https://clarity.example/')
    expect(document.querySelector('meta[property="og:image"]')).toHaveAttribute('content', 'https://clarity.example/social-card-v2.png')

    const structured = JSON.parse(document.querySelector('script[data-seo-structured-data]').textContent)
    expect(structured['@graph'].map((node) => node['@type'])).toEqual([
      'Organization',
      'WebSite',
      'SoftwareApplication',
      'ItemList',
      'FAQPage',
    ])
    // Ratings and reviews would be fabricated social proof; the published
    // seat price is a claim the pricing page already makes.
    for (const node of structured['@graph']) {
      expect(node).not.toHaveProperty('aggregateRating')
      expect(node).not.toHaveProperty('review')
    }
  })

  it('publishes structured data on marketing routes other than the home page', async () => {
    vi.stubEnv('VITE_PUBLIC_SITE_URL', 'https://clarity.example')
    renderAt('/pricing')

    await waitFor(() => expect(document.title).toBe('Pricing | LawHand Legal Automation Platform'))
    expect(document.querySelector('link[rel="canonical"]')).toHaveAttribute('href', 'https://clarity.example/pricing')

    const structured = JSON.parse(document.querySelector('script[data-seo-structured-data]').textContent)
    const types = structured['@graph'].map((node) => node['@type'])
    expect(types).toContain('FAQPage')
    expect(types).toContain('BreadcrumbList')
  })

  it('marks an unknown path noindex and describes it as missing', async () => {
    vi.stubEnv('VITE_PUBLIC_SITE_URL', 'https://clarity.example')
    renderAt('/campaign-link-that-expired')

    await waitFor(() => expect(document.title).toBe('Page not found | LawHand'))
    expect(document.querySelector('meta[name="robots"]')).toHaveAttribute('content', expect.stringContaining('noindex'))
    expect(document.querySelector('link[rel="canonical"]')).not.toBeInTheDocument()
    expect(document.querySelector('script[data-seo-structured-data]')).not.toBeInTheDocument()
  })

  it('marks authenticated and token-bearing routes noindex without a canonical URL', async () => {
    vi.stubEnv('VITE_PUBLIC_SITE_URL', 'https://clarity.example')
    renderAt('/portal/client/matter?token=do-not-publish')

    await waitFor(() => expect(document.title).toBe('Secure client portal | LawHand'))
    expect(document.querySelector('meta[name="robots"]')).toHaveAttribute('content', expect.stringContaining('noindex'))
    expect(document.querySelector('link[rel="canonical"]')).not.toBeInTheDocument()
    expect(document.head.innerHTML).not.toContain('do-not-publish')
    expect(document.querySelector('script[data-seo-structured-data]')).not.toBeInTheDocument()
  })
})
