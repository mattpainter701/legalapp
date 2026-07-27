import { describe, expect, it } from 'vitest'
import { fingerprint, stableSerialize } from '../src/contracts/fingerprint'
import { PlanValidationError, validatePlan } from '../src/contracts/validation'

const future = '2030-01-01T00:00:00.000Z'

function wordPlan(overrides: Record<string, unknown> = {}): unknown {
  return {
    planId: 'plan-1',
    surface: 'word',
    expiresAt: future,
    baseFingerprint: 'sha256:selection',
    summary: 'Replace the selected clause',
    warnings: ['Attorney review required'],
    actions: [{
      type: 'replace_selection',
      anchor: { selectionHash: 'sha256:selection' },
      content: { text: 'Replacement', format: 'text' },
    }],
    ...overrides,
  }
}

describe('validatePlan', () => {
  it('accepts a bounded Word selection replacement', () => {
    const plan = validatePlan(wordPlan(), 'word', 'sha256:selection', new Date('2029-01-01'))
    expect(plan.actions[0]?.type).toBe('replace_selection')
  })

  it('rejects arbitrary script execution', () => {
    const raw = wordPlan({
      actions: [{
        type: 'execute_script',
        anchor: { selectionHash: 'sha256:selection' },
        content: { code: 'Office.context.document.setSelectedDataAsync("x")' },
      }],
    })
    expect(() => validatePlan(raw, 'word', 'sha256:selection', new Date('2029-01-01')))
      .toThrowError(expect.objectContaining<Partial<PlanValidationError>>({ code: 'unsupported_action' }))
  })

  it('rejects cursor-position writes without a stable anchor', () => {
    const raw = wordPlan({
      actions: [{
        type: 'insert_at_cursor',
        anchor: { selectionHash: 'sha256:selection' },
        content: { text: 'Insertion', format: 'text' },
      }],
    })
    expect(() => validatePlan(raw, 'word', 'sha256:selection', new Date('2029-01-01')))
      .toThrowError(expect.objectContaining<Partial<PlanValidationError>>({ code: 'unsupported_action' }))
  })

  it('requires each action anchor to match the captured fingerprint', () => {
    const raw = wordPlan({
      actions: [{
        type: 'replace_selection',
        anchor: { selectionHash: 'sha256:different' },
        content: { text: 'Replacement', format: 'text' },
      }],
    })
    expect(() => validatePlan(raw, 'word', 'sha256:selection', new Date('2029-01-01')))
      .toThrowError(expect.objectContaining<Partial<PlanValidationError>>({ code: 'anchor_mismatch' }))
  })

  it('rejects unknown fields instead of silently ignoring them', () => {
    const raw = wordPlan({ bypassApproval: true })
    expect(() => validatePlan(raw, 'word', 'sha256:selection', new Date('2029-01-01')))
      .toThrowError(expect.objectContaining<Partial<PlanValidationError>>({ code: 'unknown_field' }))
  })

  it('rejects stale and cross-surface plans', () => {
    expect(() => validatePlan(wordPlan(), 'word', 'sha256:changed', new Date('2029-01-01')))
      .toThrowError(expect.objectContaining<Partial<PlanValidationError>>({ code: 'stale_plan' }))
    expect(() => validatePlan(wordPlan(), 'excel', 'sha256:selection', new Date('2029-01-01')))
      .toThrowError(expect.objectContaining<Partial<PlanValidationError>>({ code: 'surface_mismatch' }))
  })

  it('rejects expired plans', () => {
    expect(() => validatePlan(wordPlan(), 'word', 'sha256:selection', new Date('2031-01-01')))
      .toThrowError(expect.objectContaining<Partial<PlanValidationError>>({ code: 'expired_plan' }))
  })

  it('requires formulas to be explicit formulas', () => {
    const raw = {
      planId: 'plan-2',
      surface: 'excel',
      expiresAt: future,
      baseFingerprint: 'sha256:range',
      summary: 'Update formula',
      warnings: [],
      actions: [{
        type: 'set_selected_formulas',
        anchor: { selectionHash: 'sha256:range', address: 'Sheet1!A1' },
        content: { formulas: [['SUM(A1:A5)']] },
      }],
    }
    expect(() => validatePlan(raw, 'excel', 'sha256:range', new Date('2029-01-01')))
      .toThrowError(expect.objectContaining<Partial<PlanValidationError>>({ code: 'invalid_formula' }))
  })

  it('rejects ragged Excel matrices', () => {
    const raw = {
      planId: 'plan-3',
      surface: 'excel',
      expiresAt: future,
      baseFingerprint: 'sha256:range',
      summary: 'Update cells',
      warnings: [],
      actions: [{
        type: 'set_selected_values',
        anchor: { selectionHash: 'sha256:range', address: 'Sheet1!A1:B2' },
        content: { values: [[1, 2], [3]] },
      }],
    }
    expect(() => validatePlan(raw, 'excel', 'sha256:range', new Date('2029-01-01')))
      .toThrowError(expect.objectContaining<Partial<PlanValidationError>>({ code: 'invalid_matrix' }))
  })
})

describe('fingerprints', () => {
  it('serializes object keys deterministically', () => {
    expect(stableSerialize({ b: 2, a: { d: 4, c: 3 } }))
      .toBe(stableSerialize({ a: { c: 3, d: 4 }, b: 2 }))
  })

  it('changes when selected content changes', async () => {
    const first = await fingerprint({ text: 'alpha' })
    const second = await fingerprint({ text: 'beta' })
    expect(first).toMatch(/^sha256:[0-9a-f]{64}$/)
    expect(second).not.toBe(first)
  })
})
