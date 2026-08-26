import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { initializeAnalytics, isMeasurablePath, trackPageView } from './googleAnalytics'

/**
 * Mounts Google Analytics 4 for the public marketing site.
 *
 * The tag is not loaded at all until the visitor is on a public page, so a
 * deployment with no measurement id configured, and every signed-in session,
 * ship no third-party analytics request.
 */
export default function GoogleAnalytics() {
  const { pathname, search } = useLocation()

  useEffect(() => {
    if (!isMeasurablePath(pathname)) return
    if (!initializeAnalytics(import.meta.env.VITE_GA_MEASUREMENT_ID)) return
    trackPageView(pathname, search)
  }, [pathname, search])

  return null
}
