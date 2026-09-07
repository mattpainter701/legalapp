import { describe, expect, it } from 'vitest'
import { availableNavigation, visibleNavigation, VIEW_PRESETS, NAV_ITEMS } from './navigation'

const user = { role: 'user', enabled_modules: NAV_ITEMS.map((item) => item.module) }
describe('role and personal navigation', () => {
  it('gives reception only its six requested functions', () => {
    const items = availableNavigation({ ...user, navigation_paths: VIEW_PRESETS.Receptionist })
    expect(items.map((item) => item.path).sort()).toEqual([...VIEW_PRESETS.Receptionist].sort())
    expect(VIEW_PRESETS.Partner).toEqual(expect.arrayContaining([...VIEW_PRESETS.Attorney, '/invoices', '/trust', '/reports']))
  })
  it('cannot restore role-hidden or unlicensed functions through preferences', () => {
    expect(visibleNavigation({ ...user, enabled_modules: ['tasks'], navigation_paths: ['/tasks', '/intake'], navigation_preferences: { hidden: [], order: ['/chat', '/intake', '/tasks'] } }).map((item) => item.path)).toEqual(['/tasks'])
  })
  it('hides and reorders functions while keeping administrator recovery available', () => {
    const items = visibleNavigation({ ...user, role: 'admin', navigation_paths: ['/tasks', '/intake', '/calendar'], navigation_preferences: { hidden: ['/calendar', '/admin'], order: ['/intake', '/tasks'] } })
    expect(items.map((item) => item.path)).toEqual(['/intake', '/tasks', '/admin', '/onboarding'])
    expect(visibleNavigation({ ...user, navigation_paths: [] })).toEqual([])
    expect(availableNavigation({ ...user, navigation_paths: null })).toHaveLength(16)
    expect(availableNavigation({ role: 'admin' })).toEqual([])
  })
})
