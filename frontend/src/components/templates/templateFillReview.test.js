import { describe, expect, it } from 'vitest'
import { applyFillSuggestions, discoverySuggestions, fillReview, initialFillValues } from './templateFillReview'

describe('matter template completion', () => {
  it('counts answers independently of suggestion review, excluding signatures and linked fields', () => {
    const fields = { client: {}, fee: {}, signature: { field_type: 'signature' }, duplicate: { value_from: 'client' }, consent: { field_type: 'checkbox', required: true }, optional: { field_type: 'checkbox' } }
    const values = { client: 'Ada', fee: '', consent: 'false', optional: 'false' }
    const source = { client: { suggested_value: 'Ada', confidence: 0.82 } }
    const progress = fillReview(Object.keys(fields), fields, values, source, {})
    expect(progress).toMatchObject({ completed: 2, total: 4, percent: 50 })
    expect(progress.remaining.map(row => row.name)).toEqual(['fee', 'consent'])
    expect(progress.review).toMatchObject([{ name: 'client', confidence: 82 }])
    expect(fillReview(['client'], fields, values, source, { client: 'Ada' }).review).toEqual([])
    expect(fillReview(['client'], fields, { client: 'Grace' }, source, {}).rows[0]).toMatchObject({ source: null, confidence: null })
  })
  it.each([null, undefined, NaN, Infinity, -1, 2, '0.9'])('does not invent confidence for %s', confidence => {
    expect(fillReview(['a'], {}, { a: 'value' }, { a: { suggested_value: 'value', confidence } }, {}).rows[0].confidence).toBeNull()
  })
  it('normalizes arrays, dictionaries, booleans and zero without erasing valid values', () => {
    expect(discoverySuggestions({ variables: [{ variable: 'fee', suggested_value: 0 }, { name: 'ok', value: false }, {}] })).toMatchObject({ fee: { suggested_value: 0 }, ok: { suggested_value: false } })
    expect(discoverySuggestions({ values: { fee: 0, ok: false, name: { value: 'Ada', confidence: 0.7 } } })).toMatchObject({ fee: { suggested_value: 0 }, ok: { suggested_value: false }, name: { suggested_value: 'Ada', confidence: 0.7 } })
    expect(discoverySuggestions(null)).toEqual({})
  })
  it('keeps manual and unchecked values with their own provenance when refreshing', () => {
    const names = ['client', 'fee', 'ok']
    const fields = { ok: { field_type: 'checkbox' } }
    expect(initialFillValues(names, fields)).toEqual({ client: '', fee: '', ok: 'false' })
    const result = applyFillSuggestions(names, fields, { client: 'Manual', fee: '', ok: 'false' }, {}, discoverySuggestions({ variables: { client: 'Auto', fee: 0, ok: true } }))
    expect(result.values).toEqual({ client: 'Manual', fee: '0', ok: 'false' })
    expect(result.sources).toEqual({ fee: { suggested_value: 0 } })
  })
})
