import React from 'react'
import { render, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SeoHead from './SeoHead'

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

    await waitFor(() => expect(document.title).toContain('Law Firm Operations'))
    expect(document.querySelector('meta[name="robots"]')).toHaveAttribute('content', expect.stringContaining('index, follow'))
    expect(document.querySelector('link[rel="canonical"]')).toHaveAttribute('href', 'https://clarity.example/')
    expect(document.querySelector('meta[property="og:image"]')).toHaveAttribute('content', 'https://clarity.example/social-card.jpg')

    const structured = JSON.parse(document.querySelector('script[data-seo-structured-data]').textContent)
    expect(structured['@graph'].map((node) => node['@type'])).toEqual([
      'Organization',
      'WebSite',
      'SoftwareApplication',
    ])
    for (const node of structured['@graph']) {
      expect(node).not.toHaveProperty('aggregateRating')
      expect(node).not.toHaveProperty('review')
      expect(node).not.toHaveProperty('offers')
    }
  })

  it('marks authenticated and token-bearing routes noindex without a canonical URL', async () => {
    vi.stubEnv('VITE_PUBLIC_SITE_URL', 'https://clarity.example')
    renderAt('/portal/client/matter?token=do-not-publish')

    await waitFor(() => expect(document.title).toBe('Secure client portal | Clarity Legal'))
    expect(document.querySelector('meta[name="robots"]')).toHaveAttribute('content', expect.stringContaining('noindex'))
    expect(document.querySelector('link[rel="canonical"]')).not.toBeInTheDocument()
    expect(document.head.innerHTML).not.toContain('do-not-publish')
    expect(document.querySelector('script[data-seo-structured-data]')).not.toBeInTheDocument()
  })
})
