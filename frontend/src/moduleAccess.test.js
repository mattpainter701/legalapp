import { describe, expect, it } from 'vitest'
import { canAccessModuleList } from './moduleAccess'

describe('module visibility', () => {
  it('uses server capabilities as the authority', () => {
    expect(canAccessModuleList(['intake', 'tasks'], 'intake')).toBe(true)
    expect(canAccessModuleList(['intake', 'tasks'], 'matters')).toBe(false)
  })

  it('does not grant a missing module', () => {
    expect(canAccessModuleList([], 'admin')).toBe(false)
  })
})
