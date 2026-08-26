import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import { useAuth } from '../App'
import { initializeAnalytics, isMeasurablePath, trackPageView } from './googleAnalytics'

/**
 * Mounts Google Analytics 4 for the public marketing site.
 *
 * Two boundaries decide whether the tag may run at all, and both have to hold:
 *
 * 1. The visitor is not signed in. `/` renders the public home page while auth
 *    resolves and only then redirects a signed-in user to their workspace, so
 *    measuring on pathname alone would load the tag and report a view for every
 *    returning customer who opens the site root. Waiting for `loading` to clear
 *    keeps a signed-in session free of third-party analytics entirely, and
 *    keeps returning customers out of the marketing funnel numbers.
 *
 * 2. The document itself was served from a marketing route. A Content-Security-
 *    Policy is bound to the document, not to the URL, so a visitor who entered
 *    on a workspace route carries `script-src 'self'` for that document's whole
 *    lifetime. Injecting the tag after a client-side navigation to a marketing
 *    route would only produce a blocked request and a console error, so this
 *    does not try. Organic search traffic lands on the marketing routes
 *    directly, which is exactly the traffic worth measuring.
 */
export default function GoogleAnalytics() {
  const { pathname, search } = useLocation()
  const { user, loading } = useAuth()
  // First render is the entry navigation, so this is the path nginx chose the
  // document's CSP for.
  const entryPath = useRef(pathname)

  useEffect(() => {
    if (loading || user) return
    if (!isMeasurablePath(entryPath.current)) return
    if (!isMeasurablePath(pathname)) return
    if (!initializeAnalytics(import.meta.env.VITE_GA_MEASUREMENT_ID)) return
    trackPageView(pathname, search)
  }, [pathname, search, loading, user])

  return null
}
