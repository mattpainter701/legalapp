import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  initializeAnalytics,
  isMeasurablePath,
  normalizeMeasurementId,
  normalizeVerificationToken,
  trackAnalyticsEvent,
  trackPageView,
} from './googleAnalytics'

const MEASUREMENT_ID = 'G-XRFT19WYPH'

function resetAnalytics() {
  delete window.__lawhandAnalyticsId
  delete window.gtag
  delete window.dataLayer
  for (const node of document.querySelectorAll('script[data-google-analytics]')) {
    node.remove()
  }
}

describe('Google Analytics', () => {
  beforeEach(resetAnalytics)
  afterEach(() => {
    resetAnalytics()
    vi.restoreAllMocks()
  })

  it('accepts a GA4 measurement id and rejects anything else', () => {
    expect(normalizeMeasurementId(MEASUREMENT_ID)).toBe(MEASUREMENT_ID)
    expect(normalizeMeasurementId('  ')).toBe('')
    expect(normalizeMeasurementId(undefined)).toBe('')

    // A typo would otherwise produce a property that silently collects nothing.
    expect(() => normalizeMeasurementId('UA-12345-1')).toThrow(/GA4 measurement id/)
    expect(() => normalizeMeasurementId('g-xrft19wyph')).toThrow(/GA4 measurement id/)
    expect(() => normalizeMeasurementId('G-SHORT')).toThrow(/GA4 measurement id/)
  })

  it('accepts a bare Search Console token and rejects a pasted meta element', () => {
    expect(normalizeVerificationToken('abcdefghijklmnopqrstuvwxyz012345')).toBe(
      'abcdefghijklmnopqrstuvwxyz012345',
    )
    expect(normalizeVerificationToken('')).toBe('')
    expect(() => normalizeVerificationToken('<meta name="google-site-verification" content="x" />'))
      .toThrow(/bare token/)
    expect(() => normalizeVerificationToken('short')).toThrow(/bare token/)
  })

  it('measures public marketing pages only', () => {
    for (const path of ['/', '/product', '/product/chat', '/product/mcp', '/pricing', '/request-demo', '/privacy', '/terms']) {
      expect(isMeasurablePath(path)).toBe(true)
    }

    // A signed-in URL can carry a matter, client, or portal identifier. Sending
    // it to Google would leak firm data through the page_path dimension.
    for (const path of ['/matters', '/matters/9f3c-client-id', '/chat', '/invoices/44', '/portal/client/matter', '/login', '/admin']) {
      expect(isMeasurablePath(path)).toBe(false)
    }
  })

  it('loads the tag once and disables automatic page views', () => {
    expect(initializeAnalytics(MEASUREMENT_ID)).toBe(true)

    const scripts = document.querySelectorAll('script[data-google-analytics]')
    expect(scripts).toHaveLength(1)
    expect(scripts[0].src).toBe(`https://www.googletagmanager.com/gtag/js?id=${MEASUREMENT_ID}`)
    expect(scripts[0].async).toBe(true)

    const config = window.dataLayer.find(([command]) => command === 'config')
    expect(config[1]).toBe(MEASUREMENT_ID)
    // This is a single-page app: automatic page_view would report only the
    // entry URL and miss every client-side navigation.
    expect(config[2].send_page_view).toBe(false)
    // A firm evaluating legal software must not be swept into an ads audience.
    expect(config[2].allow_google_signals).toBe(false)
    expect(config[2].allow_ad_personalization_signals).toBe(false)

    initializeAnalytics(MEASUREMENT_ID)
    expect(document.querySelectorAll('script[data-google-analytics]')).toHaveLength(1)
  })

  it('ships no analytics request when no measurement id is configured', () => {
    expect(initializeAnalytics('')).toBe(false)
    expect(initializeAnalytics(undefined)).toBe(false)
    expect(document.querySelector('script[data-google-analytics]')).toBeNull()
    expect(trackPageView('/')).toBe(false)
    expect(trackAnalyticsEvent('demo_cta_clicked')).toBe(false)
  })

  it('never sends a page view for a signed-in workspace URL', () => {
    initializeAnalytics(MEASUREMENT_ID)
    const gtag = vi.spyOn(window, 'gtag')

    expect(trackPageView('/pricing', '?utm_source=google')).toBe(true)
    expect(gtag).toHaveBeenCalledWith('event', 'page_view', expect.objectContaining({
      page_path: '/pricing?utm_source=google',
    }))

    gtag.mockClear()
    expect(trackPageView('/matters/9f3c-client-id')).toBe(false)
    expect(gtag).not.toHaveBeenCalled()
  })

  it('forwards marketing conversions once the tag is configured', () => {
    initializeAnalytics(MEASUREMENT_ID)
    const gtag = vi.spyOn(window, 'gtag')

    expect(trackAnalyticsEvent('demo_form_submitted', { utm_source: 'google' })).toBe(true)
    expect(gtag).toHaveBeenCalledWith('event', 'demo_form_submitted', { utm_source: 'google' })
  })
})
