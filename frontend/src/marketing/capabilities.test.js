import { describe, expect, it } from 'vitest'
import {
  CAPABILITY_CATALOG,
  CAPABILITY_CATALOG_REVIEW,
  CAPABILITY_STATES,
  CORE_CAPABILITIES,
  NON_PUBLIC_CAPABILITIES,
} from './capabilities'

describe('reviewed marketing capability catalog', () => {
  it('uses every approved maturity state and records ownership', () => {
    expect(new Set(CAPABILITY_CATALOG.map(({ availability }) => availability)))
      .toEqual(new Set(Object.keys(CAPABILITY_STATES)))

    for (const capability of CAPABILITY_CATALOG) {
      expect(capability.claimOwner).toBe(CAPABILITY_CATALOG_REVIEW.owner)
      expect(capability.reviewedAt).toMatch(/^\d{4}-\d{2}-\d{2}$/)
      expect(capability.availabilityNote).toBeTruthy()
    }
  })

  it('keeps planned and internal-only capabilities out of public feature claims', () => {
    expect(CORE_CAPABILITIES.every(({ availability }) => availability !== 'planned')).toBe(true)
    expect(NON_PUBLIC_CAPABILITIES).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'court-rules-citator', availability: 'planned' }),
      expect.objectContaining({ id: 'customer-import-api', availability: 'planned' }),
      expect.objectContaining({ id: 'licensed-research-content', availability: 'partner-dependent' }),
    ]))
  })

  it('qualifies every partner-dependent claim with its external dependency', () => {
    for (const capability of CAPABILITY_CATALOG.filter(({ availability }) => availability === 'partner-dependent')) {
      expect(capability.availabilityNote).toMatch(/provider|account|consent|license|proprietary/i)
    }
  })
})
