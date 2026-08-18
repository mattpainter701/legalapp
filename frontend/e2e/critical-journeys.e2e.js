import { expect, test } from '@playwright/test'

const email = process.env.E2E_USER_EMAIL || 'reception@playwright-e2e.example.com'
const password = process.env.E2E_USER_PASSWORD || 'Playwright-Only-42!'
const otherTenantConversationId = '00000000-0000-4000-8000-0000000000e2'

async function signIn(page) {
  await page.goto('/login')
  await page.getByRole('button', { name: 'Sign in with email & password' }).click()
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/intake\/dashboard$/)
  await expect(page.getByRole('heading', { name: 'Local Intake Dashboard' })).toBeVisible()
}

test('session bootstrap restores an authenticated workspace and rejects another tenant conversation', async ({ page }) => {
  await signIn(page)

  const bootstrap = page.waitForResponse((response) => (
    response.url().endsWith('/api/auth/me') && response.request().method() === 'GET'
  ))
  await page.reload()
  expect((await bootstrap).ok()).toBeTruthy()
  await expect(page).toHaveURL(/\/intake\/dashboard$/)
  await expect(page.getByRole('heading', { name: 'Local Intake Dashboard' })).toBeVisible()

  const denied = await page.context().request.get(
    new URL(`/api/conversations/${otherTenantConversationId}`, page.url()).href,
  )
  expect(denied.status()).toBe(404)
  expect(await denied.json()).toEqual({ detail: 'Conversation not found' })
})

test.describe('mobile profile context', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('navigation reaches profile and saves professional context through the authenticated session', async ({ page }) => {
    await signIn(page)
    await page.getByRole('button', { name: 'Open sidebar' }).click()
    const drawer = page.getByRole('dialog', { name: 'Workspace navigation' })
    await expect(drawer).toBeVisible()
    await drawer.getByRole('button', { name: 'Open profile' }).click()
    await expect(page).toHaveURL(/\/profile$/)
    await expect(page.getByRole('heading', { name: 'Professional context' })).toBeVisible()

    await page.getByLabel('Professional role').fill('E2E attorney')
    const patchResponse = page.waitForResponse((response) => (
      response.url().endsWith('/api/auth/me') && response.request().method() === 'PATCH'
    ))
    await page.getByRole('button', { name: 'Save context' }).click()
    expect((await patchResponse).ok()).toBeTruthy()
    await expect(page.getByRole('status').filter({ hasText: 'Your profile context has been saved.' })).toBeVisible()
  })
})

test('chat remains usable after a deterministic stream failure', async ({ page }) => {
  const conversation = {
    id: '00000000-0000-4000-8000-0000000000c1',
    title: 'Mocked reliability check',
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    message_count: 0,
    attachment_count: 0,
  }
  let streamAttempts = 0

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

    if (path === '/api/conversations' && request.method() === 'GET') return json([conversation])
    if (path === `/api/conversations/${conversation.id}` && request.method() === 'GET') {
      return json({ conversation, messages: [] })
    }
    if (path === '/api/documents' && request.method() === 'GET') return json({ documents: [] })
    if (path === '/api/matters' && request.method() === 'GET') return json([])
    if (path === '/api/mcp/source-health' && request.method() === 'GET') {
      return json({ available: false, status: 'unavailable', sources: [], partitions: [] })
    }
    if (path === `/api/conversations/${conversation.id}/messages/stream` && request.method() === 'POST') {
      streamAttempts += 1
      if (streamAttempts === 1) {
        return route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Deterministic assistant outage' }),
        })
      }
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'data: [TOKEN]"The retry completed safely."\n\ndata: [STREAM_COMPLETE]\n\n',
      })
    }
    return route.continue()
  })

  await signIn(page)
  await page.goto(`/chat?conv=${conversation.id}`)
  await expect(page.getByLabel('Message the assistant')).toBeVisible()

  await page.getByLabel('Message the assistant').fill('Test the outage state')
  await page.getByRole('button', { name: 'Send message' }).click()
  await expect(page.getByText('Message could not be sent')).toBeVisible()
  await expect(page.getByText('An error occurred: Deterministic assistant outage')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Send message' })).toBeEnabled()

  await page.getByLabel('Message the assistant').fill('Retry after outage')
  await page.getByRole('button', { name: 'Send message' }).click()
  await expect(page.getByText('The retry completed safely.')).toBeVisible()
  expect(streamAttempts).toBe(2)
})
