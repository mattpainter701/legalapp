import { describe, expect, it } from 'vitest'
import { canAccessAddonList, canAccessModuleList, hasCapability } from './moduleAccess'

describe('module visibility', () => {
  it('uses server capabilities as the authority', () => {
    expect(canAccessModuleList(['intake', 'tasks'], 'intake')).toBe(true)
    expect(canAccessModuleList(['intake', 'tasks'], 'matters')).toBe(false)
  })

  it('does not grant a missing module', () => {
    expect(canAccessModuleList([], 'admin')).toBe(false)
  })

  it('keeps licensed add-ons separate from the native module list', () => {
    expect(canAccessAddonList(['mediation-legal'], 'mediation-legal')).toBe(true)
    expect(canAccessAddonList([], 'mediation-legal')).toBe(false)
  })

  it('uses effective server capabilities for legal approval controls', () => {
    expect(hasCapability(['approve_legal_work'], 'approve_legal_work')).toBe(true)
    expect(hasCapability(['manage_documents'], 'approve_legal_work')).toBe(false)
  })
})
