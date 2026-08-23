// The Teams API returns `detail` either as a plain string or as a structured
// object ({error, message, missing_scopes, ...}). Every panel used to reach for
// `detail` directly, which rendered "[object Object]" for exactly the failures
// an admin most needs to read.
export function errorText(err, fallback = 'Something went wrong.') {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object') {
    if (typeof detail.message === 'string' && detail.message.trim()) return detail.message
    if (Array.isArray(detail.unknown_event_types) && detail.unknown_event_types.length) {
      return `Unknown notification events: ${detail.unknown_event_types.join(', ')}`
    }
    if (Array.isArray(detail.missing_scopes) && detail.missing_scopes.length) {
      return `Missing Microsoft permissions: ${detail.missing_scopes.join(', ')}`
    }
    if (typeof detail.error === 'string' && detail.error.trim()) return detail.error
  }
  // FastAPI validation errors arrive as a list of {loc, msg}.
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0]
    if (typeof first?.msg === 'string') return first.msg
  }
  if (typeof err?.message === 'string' && err.message.trim()) return err.message
  return fallback
}

export const matterLabel = (matter) =>
  matter?.matter_name || matter?.name || matter?.slug || ''
