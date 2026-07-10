import { describe, expect, it } from 'vitest'
import { readBlobErrorDetail } from './api'

describe('binary API errors', () => {
  it('extracts the actionable detail from a JSON error blob', async () => {
    const blob = new Blob([JSON.stringify({ detail: 'This PDF has no fillable AcroForm fields.' })], { type: 'application/json' })
    expect(await readBlobErrorDetail(blob)).toBe('This PDF has no fillable AcroForm fields.')
  })
})
