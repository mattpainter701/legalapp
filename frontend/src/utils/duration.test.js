import { describe, expect, it } from 'vitest'
import { parseDuration, roundToIncrement, todayLocal } from './duration'

describe('parseDuration', () => {
  it('accepts decimal hours', () => {
    expect(parseDuration('1.5')).toBe(1.5)
    expect(parseDuration('2')).toBe(2)
    expect(parseDuration('.25')).toBe(0.25)
  })

  it('accepts clock notation', () => {
    expect(parseDuration('1:30')).toBe(1.5)
    expect(parseDuration('0:06')).toBeCloseTo(0.1, 5)
  })

  it('accepts minute and hour suffixes', () => {
    expect(parseDuration('90m')).toBe(1.5)
    expect(parseDuration('45min')).toBe(0.75)
    expect(parseDuration('2h')).toBe(2)
    expect(parseDuration('1h15m')).toBe(1.25)
    expect(parseDuration('1h 15m')).toBe(1.25)
  })

  it('rejects what is not a duration', () => {
    expect(parseDuration('')).toBeNull()
    expect(parseDuration('abc')).toBeNull()
    expect(parseDuration('1:75')).toBeNull()
    expect(parseDuration(null)).toBeNull()
  })
})

describe('roundToIncrement', () => {
  it('rounds up to the six-minute default', () => {
    expect(roundToIncrement(0.02)).toBeCloseTo(0.1, 5)
    expect(roundToIncrement(0.1)).toBeCloseTo(0.1, 5)
    expect(roundToIncrement(0.11)).toBeCloseTo(0.2, 5)
  })

  it('honours a firm that bills in fifteen-minute units', () => {
    expect(roundToIncrement(0.1, 15)).toBeCloseTo(0.25, 5)
    expect(roundToIncrement(0.3, 15)).toBeCloseTo(0.5, 5)
  })

  it('leaves an exact increment alone', () => {
    expect(roundToIncrement(1.5, 15)).toBeCloseTo(1.5, 5)
  })
})

describe('todayLocal', () => {
  it('uses local date parts, not the UTC calendar day', () => {
    // 2026-09-12 21:30 local. toISOString() in a negative-offset zone would
    // report the 13th; the entry belongs on the 12th.
    const evening = new Date(2026, 8, 12, 21, 30, 0)
    expect(todayLocal(evening)).toBe('2026-09-12')
  })
})
