import { test, expect } from '@playwright/test'

test('administrator creates and edits a role view', async ({ page }) => {
  const roles = []
  const user = { id: 'admin', tenant_id: 'firm', role: 'admin', capabilities: ['manage_roles', 'admin_settings'], enabled_modules: ['admin'], full_name: 'Firm Administrator' }
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/auth/me') return route.fulfill({ json: user })
    if (path === '/api/admin/roles') {
      if (route.request().method() === 'POST') roles.push({ ...route.request().postDataJSON(), id: 'reception' })
      return route.fulfill({ json: route.request().method() === 'GET' ? roles : roles[0] })
    }
    if (path === '/api/admin/roles/reception') {
      roles[0] = { ...route.request().postDataJSON(), id: 'reception' }
      return route.fulfill({ json: roles[0] })
    }
    if (path.includes('release')) return route.fulfill({ json: { releases: [] } })
    return route.fulfill({ json: {} })
  })
  await page.goto('/admin?tab=roles')
  await page.getByRole('combobox', { name: 'View starting point' }).selectOption('Receptionist')
  await page.screenshot({ path: 'test-results/navigation-admin.png', fullPage: true })
  await page.getByRole('button', { name: 'Create role', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Role saved')
  expect(roles[0].navigation_paths).toHaveLength(6)
  await page.getByRole('button', { name: 'Edit Receptionist', exact: true }).click()
  await page.getByRole('checkbox', { name: 'Calendar', exact: true }).uncheck()
  await page.getByRole('button', { name: 'Save role', exact: true }).click()
  await expect(page.getByRole('cell', { name: '5 functions', exact: true })).toBeVisible()
})

for (const width of [390, 1440]) {
  test(`role view and personal layout persist at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const user = {
      id: 'navigation-user', tenant_id: 'navigation-firm', full_name: 'Receptionist', email: 'reception@example.test', role: 'user',
      enabled_modules: ['matters', 'chat', 'calendar', 'tasks', 'contacts', 'intake', 'intake-dashboard'],
      navigation_paths: ['/intake', '/intake/dashboard', '/conflicts', '/clients', '/tasks', '/calendar'],
      navigation_preferences: { hidden: [], order: [] },
    }
    const errors = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname
      if (path === '/api/auth/me') {
        if (route.request().method() === 'PATCH') user.navigation_preferences = route.request().postDataJSON().navigation_preferences
        return route.fulfill({ json: user })
      }
      if (path.includes('release')) return route.fulfill({ json: { releases: [] } })
      return route.fulfill({ json: [] })
    })
    await page.goto('/profile')
    const openDrawer = async () => { if (width < 1024) await page.getByRole('button', { name: 'Open sidebar', exact: true }).click() }
    await openDrawer()
    await expect(page.getByRole('button', { name: 'Assistant', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'My Matters', exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: 'Customize navigation', exact: true }).click()
    await expect(page.getByRole('checkbox')).toHaveCount(6)
    await page.getByRole('checkbox', { name: 'Calendar', exact: true }).uncheck()
    await page.getByRole('button', { name: 'Move Tasks up', exact: true }).click()
    await page.screenshot({ path: `test-results/navigation-editor-${width}.png`, fullPage: true })
    await page.getByRole('button', { name: 'Save layout', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Calendar', exact: true })).toHaveCount(0)
    await page.reload()
    await openDrawer()
    await expect(page.getByRole('button', { name: 'Calendar', exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: 'Customize navigation', exact: true }).click()
    await expect(page.getByRole('checkbox').first()).toHaveAccessibleName('Tasks')
    await expect(page.getByRole('checkbox', { name: 'Calendar', exact: true })).not.toBeChecked()
    await page.getByRole('button', { name: 'Reset to role defaults' }).click()
    await page.getByRole('button', { name: 'Save layout', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Calendar', exact: true }).first()).toBeVisible()
    expect(errors).toEqual([])
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  })
}
