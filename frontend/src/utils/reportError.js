// Central funnel for client-side error reporting.
// To ship errors to Sentry (or similar), assign window.__LAWHAND_ERROR_REPORTER__
// once at the edge — every call site already flows through here.
export function reportError(...args) {
  if (
    typeof window !== 'undefined' &&
    typeof window.__LAWHAND_ERROR_REPORTER__ === 'function'
  ) {
    try {
      window.__LAWHAND_ERROR_REPORTER__(...args)
    } catch {
      // The reporter must never break the app it observes.
    }
  }
  console.error(...args)
}
