// ── Duration parsing ────────────────────────────────────────────────────────

const DEFAULT_ROUNDING_MINUTES = 6

/** Today in the user's own timezone. `toISOString()` would roll a US evening
 *  over to tomorrow and bill the work on the wrong day. */
export function todayLocal(now = new Date()) {
  const pad = (n) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/**
 * Parse the ways timekeepers actually write a duration:
 * `1.5`, `1:30`, `90m`, `1h15m`, `1h`, `45min`. Returns hours, or null.
 */
export function parseDuration(input) {
  if (input == null) return null
  const text = String(input).trim().toLowerCase()
  if (!text) return null

  // h:mm
  const clock = text.match(/^(\d+):([0-5]?\d)$/)
  if (clock) {
    return Number(clock[1]) + Number(clock[2]) / 60
  }

  // 1h15m / 1h 15m / 1h / 90m / 45min
  const composite = text.match(/^(?:(\d+(?:\.\d+)?)\s*h(?:ours?|rs?)?)?\s*(?:(\d+(?:\.\d+)?)\s*m(?:in(?:ute)?s?)?)?$/)
  if (composite && (composite[1] || composite[2])) {
    const h = composite[1] ? Number(composite[1]) : 0
    const m = composite[2] ? Number(composite[2]) : 0
    return h + m / 60
  }

  // Bare decimal hours
  const decimal = text.match(/^\d*\.?\d+$/)
  if (decimal) return Number(text)

  return null
}

/** Round hours UP to the firm's billing increment, the way the timer does. */
export function roundToIncrement(hours, roundingMinutes = DEFAULT_ROUNDING_MINUTES) {
  const minutes = roundingMinutes > 0 ? roundingMinutes : DEFAULT_ROUNDING_MINUTES
  const increment = minutes / 60
  const rounded = Math.ceil((hours - 1e-9) / increment) * increment
  return Math.round(rounded * 100) / 100
}
