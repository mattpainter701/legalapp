import { expect, test } from '@playwright/test'

const email = process.env.E2E_USER_EMAIL || 'reception@playwright-e2e.example.com'
const password = process.env.E2E_USER_PASSWORD || 'Playwright-Only-42!'

async function signInWithKeyboard(page) {
  await page.goto('/login')
  const emailLogin = page.getByRole('button', { name: 'Sign in with email & password' })
  await emailLogin.focus()
  await expect(emailLogin).toBeFocused()
  await page.keyboard.press('Enter')

  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  const submit = page.getByRole('button', { name: 'Sign In' })
  await submit.focus()
  await expect(submit).toBeFocused()
  await page.keyboard.press('Enter')

  await expect(page).toHaveURL(/\/intake\/dashboard$/)
  await expect(page.getByRole('heading', { name: 'Local Intake Dashboard' })).toBeVisible()
  // Draft hydration is part of page readiness. Inputs are backed by the active
  // draft, so do not race the API that creates/restores it after navigation.
  await expect(page.getByRole('button', { name: 'New call' })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Discard' })).toBeVisible()
}

test('receptionist captures a caller and sees the assigned task', async ({ page }) => {
  await signInWithKeyboard(page)

  const caller = 'Jordan Rivera E2E'
  await page.getByLabel('Caller', { exact: true }).fill(caller)
  await page.getByLabel('Practice Area').selectOption('family')
  await page.getByRole('button', { name: 'Create lead', exact: true }).click()
  await page.getByLabel('Purpose').fill('Needs a family-law consultation next week')
  await page.getByLabel('Internal Notes').fill('Preferred callback window is after 2 PM')
  await page.getByLabel('Task / Routing').selectOption('specific_staff')
  await page.getByLabel('Assign To').fill('Casey')
  await page.getByRole('button', { name: 'Casey Attorney' }).click()
  await page.getByLabel('Task', { exact: true }).selectOption('Call back caller')

  const createResponse = page.waitForResponse((response) => (
    response.request().method() === 'POST'
      && response.url().endsWith('/api/intake/dashboard/calls')
  ))
  await page.getByRole('button', { name: 'Create Lead + Staff Task' }).click()
  const response = await createResponse
  expect(response.ok()).toBeTruthy()
  const created = await response.json()
  expect(created.created_lead).toBe(true)
  expect(created.task_id).toBeTruthy()

  await expect(page.locator('main').getByText(
    'Lead created. General task assigned to Casey Attorney.',
    { exact: true },
  )).toBeVisible()
  await page.locator('nav').first().getByRole('button', { name: 'Tasks', exact: true }).click()
  await expect(page).toHaveURL(/\/tasks$/)
  await expect(page.getByRole('heading', { name: 'Tasks & Deadlines' })).toBeVisible()
  await expect(page.getByText(`${caller} - Call back caller`, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Board', exact: true }).click()
  await expect(page.getByLabel('Legal work board')).toBeVisible()
  await expect(page.getByText(`${caller} - Call back caller`, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'List', exact: true }).click()
})

test.describe('mobile workspace', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('navigation is responsive and the drawer works from the keyboard', async ({ page }) => {
    await signInWithKeyboard(page)

    const noHorizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
    expect(noHorizontalOverflow).toBe(true)

    const openSidebar = page.getByRole('button', { name: 'Open sidebar' })
    await openSidebar.focus()
    await page.keyboard.press('Enter')
    const drawer = page.getByRole('dialog', { name: 'Workspace navigation' })
    await expect(drawer).toBeVisible()
    await expect(drawer.getByRole('button', { name: 'Close sidebar' })).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(drawer).toBeHidden()
    await expect(openSidebar).toBeFocused()

    const mobileNavigation = page.locator('main + nav')
    await mobileNavigation.getByRole('button', { name: 'Tasks', exact: true }).click()
    await expect(page).toHaveURL(/\/tasks$/)
    await expect(page.getByRole('heading', { name: 'Tasks & Deadlines' })).toBeVisible()
    await page.getByRole('button', { name: 'Board', exact: true }).click()
    await expect(page.getByLabel('Work stage')).toBeVisible()
    expect(await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )).toBe(true)
  })
})
