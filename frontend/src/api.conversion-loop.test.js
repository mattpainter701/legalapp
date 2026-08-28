import { describe, expect, it, vi } from 'vitest'

vi.mock('./api', async () => {
  const actual = await vi.importActual('./api')
  return { ...actual }
})

describe('conversion loop API surface', () => {
  it('exports public intake, consent, triage, and funnel operations', async () => {
    const api = await import('./api')
    for (const name of [
      'getIntakeForms', 'createIntakeForm', 'getPublicIntakeForm',
      'submitPublicIntake', 'getPublicIntakeAvailability',
      'bookPublicIntakeAppointment', 'updateLeadConsent', 'triageLead',
      'getLeadFunnel',
    ]) expect(api[name]).toEqual(expect.any(Function))
  })
})
