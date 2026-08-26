/**
 * Google Analytics 4 for the public marketing site only.
 *
 * LawHand is a legal product: a signed-in URL can carry a matter, client, or
 * portal identifier, and a page_view sends the full path. So the tag is loaded
 * lazily and fires only on the public, indexable marketing routes.
 *
 * `GoogleAnalytics.jsx` adds the two gates that pathname alone cannot express:
 * a signed-in session is never measured at all, and the tag is never injected
 * into a document whose Content-Security-Policy was chosen for a workspace
 * route. See that component for why each one is necessary.
 *
 * `config.js` is shared with the Node build, so this module is likewise free of
 * `import.meta.env` reads at module scope; callers pass the configured id.
 */
import { getRouteMeta, normalizePathname } from '../seo/config.js'

/** GA4 measurement ids look like `G-` plus 8-12 uppercase alphanumerics. */
const MEASUREMENT_ID_PATTERN = /^G-[A-Z0-9]{8,12}$/

/** Search Console HTML-tag verification tokens are URL-safe base64-ish. */
const VERIFICATION_TOKEN_PATTERN = /^[A-Za-z0-9_-]{20,100}$/

const SCRIPT_ATTRIBUTE = 'data-google-analytics'

/**
 * Accept only a well-formed measurement id.
 *
 * A typo silently produces a property that collects nothing, which is
 * indistinguishable from "no traffic yet" for weeks. Failing the build is the
 * cheaper outcome.
 */
export function normalizeMeasurementId(value) {
  const id = String(value ?? '').trim()
  if (!id) return ''
  if (!MEASUREMENT_ID_PATTERN.test(id)) {
    throw new Error(
      'VITE_GA_MEASUREMENT_ID must be a GA4 measurement id such as G-XXXXXXXXXX.',
    )
  }
  return id
}

export function normalizeVerificationToken(value) {
  const token = String(value ?? '').trim()
  if (!token) return ''
  if (!VERIFICATION_TOKEN_PATTERN.test(token)) {
    throw new Error(
      'VITE_GOOGLE_SITE_VERIFICATION must be the bare token from the Search Console '
      + 'HTML tag method, not the whole <meta> element.',
    )
  }
  return token
}

/** True when this path is a public marketing page rather than a firm workspace. */
export function isMeasurablePath(pathname) {
  return getRouteMeta(normalizePathname(pathname)).indexable === true
}

function ensureGtag() {
  window.dataLayer = window.dataLayer || []
  if (typeof window.gtag !== 'function') {
    // The documented shim: gtag must forward `arguments` verbatim, so a rest
    // parameter would change what GA receives.
    window.gtag = function gtag() {
      window.dataLayer.push(arguments)
    }
  }
  return window.gtag
}

function loadScript(measurementId) {
  if (document.querySelector(`script[${SCRIPT_ATTRIBUTE}]`)) return
  const script = document.createElement('script')
  script.async = true
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`
  script.setAttribute(SCRIPT_ATTRIBUTE, measurementId)
  document.head.appendChild(script)
}

/**
 * Load and configure GA4 once, on the first public page view.
 *
 * Automatic page_view is disabled: this is a single-page app, so the tag would
 * otherwise report only the entry URL and then miss every client-side
 * navigation. `trackPageView` sends them explicitly instead.
 */
export function initializeAnalytics(measurementId) {
  if (typeof window === 'undefined' || typeof document === 'undefined') return false
  const id = normalizeMeasurementId(measurementId)
  if (!id) return false
  if (window.__lawhandAnalyticsId === id) return true

  loadScript(id)
  const gtag = ensureGtag()
  gtag('js', new Date())
  gtag('config', id, {
    send_page_view: false,
    // Google Signals adds advertising and cross-device identity to the
    // property. A law firm evaluating legal software should not be pushed into
    // an ads audience, so it stays off.
    allow_google_signals: false,
    allow_ad_personalization_signals: false,
    cookie_flags: 'SameSite=Lax;Secure',
  })
  window.__lawhandAnalyticsId = id
  return true
}

/** Send one page_view, but never for a signed-in workspace URL. */
export function trackPageView(pathname, search = '') {
  if (typeof window === 'undefined') return false
  if (!window.__lawhandAnalyticsId) return false
  if (!isMeasurablePath(pathname)) return false

  window.gtag('event', 'page_view', {
    page_path: `${pathname}${search}`,
    page_location: window.location.href,
    page_title: document.title,
  })
  return true
}

/** Forward a marketing conversion event to GA4, if the tag is configured. */
export function trackAnalyticsEvent(name, properties = {}) {
  if (typeof window === 'undefined') return false
  if (!window.__lawhandAnalyticsId || typeof window.gtag !== 'function') return false
  window.gtag('event', name, properties)
  return true
}
