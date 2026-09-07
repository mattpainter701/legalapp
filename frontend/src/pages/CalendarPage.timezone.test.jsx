import { describe, expect, it } from 'vitest'
import { localDateTimeToIso } from './CalendarPage'

describe('localDateTimeToIso', () => {
  it('turns the calendar form wall-clock fields into an offset-bearing instant', () => {
    const expected = new Date(2026, 8, 7, 8, 30, 0, 0).toISOString()

    expect(localDateTimeToIso('2026-09-07', '08:30')).toBe(expected)
  })
})
