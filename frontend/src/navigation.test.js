import { describe, expect, it } from 'vitest'
import { availableNavigation, visibleNavigation, mobileNavigation, isNavigationActive, VIEW_PRESETS, NAV_ITEMS } from './navigation'

const user = { role: 'user', enabled_modules: NAV_ITEMS.map((item) => item.module) }
describe('role and personal navigation', () => {
  it('highlights only Call Intake when both intake functions are shown on a phone', () => {
    const items = mobileNavigation({ ...user, navigation_paths: ['/intake', '/intake/dashboard'] })
    expect(items.filter((item) => isNavigationActive(item.path, '/intake/dashboard')).map((item) => item.path)).toEqual(['/intake/dashboard'])
    expect(isNavigationActive('/intake', '/intake')).toBe(true)
    expect(isNavigationActive('/matters', '/matters/123')).toBe(true)
    expect(mobileNavigation(user).map((item) => item.path)).toEqual(['/intake/dashboard', '/matters', '/chat', '/calendar', '/tasks'])
  })
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
