// Human-readable matter numbers ("SMIT0001").
//
// Mirrors backend/app/services/matter_number.py. The two must agree on what
// counts as a matter number, because the same string decides whether a
// /matters/:id URL is resolved by number or treated as a UUID.

// Four-character prefix -- always letter-initial, possibly carrying collision
// digits in its tail -- then at least four digits of sequence.
const MATTER_NUMBER = /^[A-Z][A-Z0-9]{3}[0-9]{4,}$/

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** True when `value` is a canonical UUID, the form matter routes use natively. */
export function isUuid(value) {
  return typeof value === 'string' && UUID.test(value.trim())
}

/**
 * Fold user-typed input to canonical form, or return null if it cannot be a
 * matter number. Accepts the shapes people actually type -- lowercase,
 * hyphenated ("smit-0001"), padded -- since this arrives from an address bar
 * or a copy-paste out of an email.
 */
export function normalizeMatterNumber(value) {
  if (typeof value !== 'string') return null
  if (isUuid(value)) return null
  const candidate = value.replace(/[\s\-_]/g, '').toUpperCase()
  return MATTER_NUMBER.test(candidate) ? candidate : null
}

/** True when a /matters/:id segment should be resolved as a matter number. */
export function looksLikeMatterNumber(value) {
  return normalizeMatterNumber(value) !== null
}
