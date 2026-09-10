import { describe, it, expect } from 'vitest'
import { isUuid, normalizeMatterNumber, looksLikeMatterNumber } from './matterNumber'

describe('normalizeMatterNumber', () => {
  it('accepts the canonical form', () => {
    expect(normalizeMatterNumber('SMIT0001')).toBe('SMIT0001')
  })

  it('folds the shapes people actually type', () => {
    // Address bars lowercase, email clients hyphenate, copy-paste adds spaces.
    expect(normalizeMatterNumber('smit0001')).toBe('SMIT0001')
    expect(normalizeMatterNumber('smit-0001')).toBe('SMIT0001')
    expect(normalizeMatterNumber(' SMIT 0001 ')).toBe('SMIT0001')
    expect(normalizeMatterNumber('SMIT_0001')).toBe('SMIT0001')
  })

  it('accepts a collision-suffixed prefix', () => {
    // The second "Smith" firm gets SMI2, the tenth SM10.
    expect(normalizeMatterNumber('SMI20001')).toBe('SMI20001')
    expect(normalizeMatterNumber('SM100042')).toBe('SM100042')
  })

  it('accepts a sequence past four digits', () => {
    expect(normalizeMatterNumber('SMIT12345')).toBe('SMIT12345')
  })

  it('rejects a UUID even though stripping hyphens would shorten it', () => {
    expect(normalizeMatterNumber('550e8400-e29b-41d4-a716-446655440000')).toBeNull()
    expect(normalizeMatterNumber('abcd8400-1234-4111-8123-446655440000')).toBeNull()
  })

  it('rejects anything not shaped like a matter number', () => {
    for (const value of ['', 'SMIT', 'SMIT001', '0001SMIT', '1MIT0001', 'my', 'stats', 'field-options', 'by-number']) {
      expect(normalizeMatterNumber(value)).toBeNull()
    }
  })

  it('rejects non-strings', () => {
    expect(normalizeMatterNumber(null)).toBeNull()
    expect(normalizeMatterNumber(undefined)).toBeNull()
    expect(normalizeMatterNumber(12345678)).toBeNull()
  })
})

describe('looksLikeMatterNumber', () => {
  it('separates matter numbers from the UUIDs matter routes use natively', () => {
    expect(looksLikeMatterNumber('SMIT0001')).toBe(true)
    expect(looksLikeMatterNumber('550e8400-e29b-41d4-a716-446655440000')).toBe(false)
  })

  it('does not swallow the sibling routes registered under /matters', () => {
    // /matters/my and /matters/stats must never be resolved as matter numbers.
    expect(looksLikeMatterNumber('my')).toBe(false)
    expect(looksLikeMatterNumber('stats')).toBe(false)
  })
})

describe('isUuid', () => {
  it('recognises canonical UUIDs, case-insensitively', () => {
    expect(isUuid('550e8400-e29b-41d4-a716-446655440000')).toBe(true)
    expect(isUuid('550E8400-E29B-41D4-A716-446655440000')).toBe(true)
  })

  it('rejects matter numbers and malformed input', () => {
    expect(isUuid('SMIT0001')).toBe(false)
    expect(isUuid('550e8400e29b41d4a716446655440000')).toBe(false)
    expect(isUuid(null)).toBe(false)
  })
})
