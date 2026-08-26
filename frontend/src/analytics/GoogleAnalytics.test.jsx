import { cleanup, render, waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({ current: { user: null, loading: false } }))
vi.mock('../App', () => ({ useAuth: () => auth.current }))

const MEASUREMENT_ID = 'G-XRFT19WYPH'

let GoogleAnalytics

beforeEach(async () => {
  vi.stubEnv('VITE_GA_MEASUREMENT_ID', MEASUREMENT_ID)
  auth.current = { user: null, loading: false }
  delete window.__lawhandAnalyticsId
  delete window.gtag
  delete window.dataLayer
  for (const node of document.querySelectorAll('script[data-google-analytics]')) node.remove()
  ;({ default: GoogleAnalytics } = await import('./GoogleAnalytics'))
})

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
})

/** Performs a client-side route change, which serves no new document. */
function NavigateAfterMount({ to }) {
  const navigate = useNavigate()
  useEffect(() => { navigate(to) }, [navigate, to])
  return null
}

function renderEntering(entryPath, thenNavigateTo) {
  return render(
    <MemoryRouter initialEntries={[entryPath]}>
      <GoogleAnalytics />
      <NavigateAfterMount to={thenNavigateTo} />
    </MemoryRouter>,
  )
}

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <GoogleAnalytics />
    </MemoryRouter>,
  )
}

const tagLoaded = () => document.querySelectorAll('script[data-google-analytics]').length
const pageViews = () =>
  (window.dataLayer || []).filter((entry) => entry[0] === 'event' && entry[1] === 'page_view')

describe('GoogleAnalytics mounting', () => {
  it('measures an anonymous visitor on a marketing page', async () => {
    renderAt('/pricing')

    await waitFor(() => expect(tagLoaded()).toBe(1))
    expect(pageViews()).toHaveLength(1)
  })

  it('never loads the tag for a signed-in visitor on the site root', async () => {
    // `/` renders the public home page until auth resolves and only then
    // redirects to the workspace, so pathname alone would report a view for
    // every returning customer who opens the site root.
    auth.current = { user: { id: 'u1' }, loading: false }
    renderAt('/')

    await waitFor(() => expect(tagLoaded()).toBe(0))
    expect(pageViews()).toHaveLength(0)
  })

  it('waits for authentication to resolve before measuring the site root', async () => {
    auth.current = { user: null, loading: true }
    const view = renderAt('/')

    await waitFor(() => expect(tagLoaded()).toBe(0))

    auth.current = { user: { id: 'u1' }, loading: false }
    view.rerender(
      <MemoryRouter initialEntries={['/']}>
        <GoogleAnalytics />
      </MemoryRouter>,
    )

    await waitFor(() => expect(tagLoaded()).toBe(0))
    expect(pageViews()).toHaveLength(0)
  })

  it('does not inject the tag into a document whose CSP would block it', async () => {
    // A Content-Security-Policy is bound to the document, not the URL. Entering
    // on a workspace route means `script-src 'self'` for that document's whole
    // lifetime, so a later client-side navigation to a marketing route must not
    // attempt an injection that nginx has already forbidden.
    renderEntering('/login', '/pricing')

    await waitFor(() => expect(tagLoaded()).toBe(0))
    expect(pageViews()).toHaveLength(0)
  })

  it('keeps measuring across client-side navigation within the marketing site', async () => {
    // The same document, so its CSP already permits the tag. Automatic
    // page_view is disabled, so each route change must be reported explicitly
    // or every page after the entry one goes missing.
    renderEntering('/', '/pricing')

    await waitFor(() => expect(pageViews()).toHaveLength(2))
    expect(tagLoaded()).toBe(1)
    expect(pageViews().map((entry) => entry[2].page_path)).toEqual(['/', '/pricing'])
  })
})
