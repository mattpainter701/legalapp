import { test, expect } from '@playwright/test'
import { dismissReleaseAnnouncement } from './release-announcement.js'

const matterId = '00000000-0000-4000-8000-000000000001'
const matter = { id: matterId, matter_name: 'Synthetic Smith matter', status: 'open', stage: 'discovery', client_email: 'client@example.test', assignments: [], key_dates: {} }
const user = { id: 'user-1', full_name: 'Solo Attorney', role: 'admin', capabilities: ['manage_matters', 'approve_legal_work', 'view_matters'], enabled_modules: ['matters', 'tasks', 'documents', 'chat'] }
const json = (route, data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })

async function fixture(page, { denied = false } = {}) {
  const state = { notes: [], requests: [], sends: [], mutations: [], taskQueries: [] }
  await page.context().route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (path === '/api/auth/me') return json(route, user)
    if (path === `/api/matters/${matterId}`) return json(route, matter)
    if (path === '/api/matters' || path === '/api/matters/my') return json(route, { items: [matter] })
    if (path.endsWith('/notes') && request.method() === 'POST') {
      const data = request.postDataJSON(); state.requests.push(data)
      if (denied) return json(route, { detail: 'Forbidden' }, 403)
      // Emulate server commit followed by lost response, then idempotent replay.
      if (!state.notes.some(note => note.id === data.request_id)) state.notes.push({ id: data.request_id, title: `Note: ${data.title}`, content: data.content, entry_type: 'note', created_at: '2026-09-06T12:00:00Z' })
      if (state.requests.length === 1) return route.abort('failed')
      return json(route, { id: data.request_id }, 201)
    }
    if (path.endsWith('/timeline')) return json(route, state.notes)
    if (path.endsWith('/email-client')) { state.sends.push(request.postDataJSON()); return json(route, { sent: false, delivery_error: 'Synthetic delivery unavailable; nothing sent.' }) }
    if (request.method() !== 'GET') { state.mutations.push(path); return json(route, {}) }
    if (path.endsWith('/document-folders')) return json(route, { items: [], root_document_count: 1 })
    if (path === `/api/matters/${matterId}/documents`) return json(route, { items: [{ id: 'doc-1', filename: 'Authorized case note.txt', file_size: 32, content_type: 'text/plain', storage_backend: 'local', created_at: '2026-09-06T12:00:00Z' }] })
    if (path.endsWith('/documents/doc-1/download')) return route.fulfill({ contentType: 'text/plain', body: 'Synthetic authorized case document.' })
    if (path.endsWith('/dashboard-summary')) return json(route, { active_workers: [], upcoming_deadlines: [] })
    if (path.endsWith('/budget')) return json(route, {})
    if (path.endsWith('/cloud-files')) return json(route, { connected: false, files: [] })
    if (path.endsWith('/cloud-folder')) return json(route, null)
    if (path.endsWith('/document-tags')) return json(route, { items: [] })
    if (path === '/api/tasks' || path === '/api/tasks/overdue') { state.taskQueries.push(new URL(request.url()).searchParams.get('matter_id')); return json(route, { items: [], total: 0 }) }
    if (path === '/api/tasks/board') return json(route, { columns: [], risk_counts: {} })
    return json(route, [])
  })
  await page.goto(`/matters/${matterId}`)
  await dismissReleaseAnnouncement(page)
  await expect(page.getByRole('heading', { name: matter.matter_name })).toBeVisible()
  return state
}

for (const width of [360, 390]) {
  test(`phone casework at ${width}px retains interrupted note and reaches authorized work`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    const state = await fixture(page)
    const nav = page.getByRole('navigation', { name: 'Mobile matter casework' })
    await expect(nav).toContainText('Stage: discovery')
    if (process.env.MOBILE_REVIEW_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.MOBILE_REVIEW_ARTIFACT_DIR}/mobile-matter-${width}.png`, fullPage: true })
    for (const button of await nav.getByRole('button').all()) expect((await button.boundingBox()).height).toBeGreaterThanOrEqual(44)
    await nav.getByRole('button', { name: 'Quick note' }).click()
    await page.getByLabel('Title', { exact: true }).fill('Client called')
    await page.getByLabel('Content', { exact: true }).fill('Confirmed the hearing preparation meeting.')
    await page.getByRole('button', { name: 'Save Note', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('Save not confirmed')
    await expect(page.getByLabel('Content', { exact: true })).toHaveValue('Confirmed the hearing preparation meeting.')
    await page.getByRole('button', { name: 'Save Note', exact: true }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Note saved.' })).toBeVisible()
    expect(state.requests).toHaveLength(2)
    expect(state.requests[1]).toEqual(state.requests[0])
    expect(state.notes).toHaveLength(1)
    await expect(page.getByText('Note: Client called', { exact: true })).toBeVisible()
    await nav.getByRole('button', { name: 'Read documents' }).click()
    await expect(page.getByText('Authorized case note.txt', { exact: true }).first()).toBeVisible()
    const download = page.getByRole('link', { name: 'Download', exact: true }).filter({ visible: true })
    const popupPromise = page.waitForEvent('popup')
    await download.click()
    const popup = await popupPromise
    await expect(popup.locator('body')).toContainText('Synthetic authorized case document.')
    await popup.close()
    await nav.getByRole('button', { name: 'Review work' }).click()
    await expect(page.getByRole('heading', { name: 'Matter workflow' })).toBeVisible()
    expect(state.mutations).toEqual([])
    await nav.getByRole('button', { name: 'Contact client' }).click()
    const dialog = page.getByRole('dialog', { name: 'Email Client' })
    await dialog.getByLabel('Message', { exact: true }).fill('Please confirm our meeting.')
    expect(state.sends).toHaveLength(0)
    // A short viewport approximates keyboard space; it is not a real iOS keyboard test.
    await page.setViewportSize({ width, height: 430 })
    await dialog.getByRole('button', { name: 'Send', exact: true }).click()
    await expect(dialog).toContainText('Synthetic delivery unavailable; nothing sent.')
    await expect(dialog.getByLabel('Message', { exact: true })).toHaveValue('Please confirm our meeting.')
    expect(state.sends).toHaveLength(1)
    await dialog.getByRole('button', { name: 'Close email composer' }).click()
    await page.setViewportSize({ width, height: 844 })
    await nav.getByRole('button', { name: 'Manage tasks' }).click()
    await expect(page).toHaveURL(new RegExp(`/tasks\\?matter_id=${matterId}`))
    await expect(page.getByLabel('Filter tasks by matter')).toHaveValue(matterId)
    expect(state.taskQueries.slice(-2)).toEqual([matterId, matterId])
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}

test('denied note retains text and never reports saved', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 760 })
  const state = await fixture(page, { denied: true })
  await page.getByRole('button', { name: 'Quick note' }).click()
  await page.getByLabel('Title', { exact: true }).fill('Private note')
  await page.getByLabel('Content', { exact: true }).fill('Retain this text')
  await page.getByRole('button', { name: 'Save Note', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('do not have permission')
  await expect(page.getByLabel('Content', { exact: true })).toHaveValue('Retain this text')
  expect(state.notes).toEqual([])
  expect(state.sends).toEqual([])
})

const taskId = '00000000-0000-4000-8000-0000000000d1'
const conversationId = '00000000-0000-4000-8000-0000000000c2'
const source = {
  source_id: 'source-demo-1',
  label: 'Synthetic engagement letter',
  url: `/api/documents/source-demo-1/download`,
  citation: 'Engagement letter, p. 2',
  locator: 'paragraph 4',
}

const proposal = {
  task_id: taskId,
  title: 'Review the synthetic engagement letter',
  status: 'review',
  version: 3,
  action_type: null,
  pending_action: null,
  sources: [source],
  approval_effect: 'Approving moves this task into active work. Nothing is sent.',
}

const task = {
  id: taskId,
  title: proposal.title,
  task_type: 'review',
  status: 'review',
  priority: 'medium',
  due_date: null,
  due_time: null,
  matter_id: 'matter-demo-1',
  contact_id: null,
  assigned_to_user_id: 'demo-user-1',
  reviewer_user_id: null,
  matter: { id: 'matter-demo-1', label: 'Synthetic matter', case_number: 'DEMO-001' },
  assignee: { id: 'demo-user-1', label: 'Demo Reviewer' },
  reviewer: null,
  version: 3,
  source: 'assistant',
  pending_action: null,
  updated_at: '2099-01-01T00:00:00Z',
}

test('phone reviews prepared work explicitly then completes the matching task', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 844 })
  let transitions = 0
  let completions = 0
  let currentTask = { ...task }
  const conversation = { id: conversationId, title: 'Synthetic source review', message_count: 1, attachment_count: 0 }
  const message = {
    id: 'message-demo-1',
    conversation_id: conversationId,
    role: 'assistant',
    content: 'The synthetic engagement letter supports the requested review.',
    sources: [source],
    proposed_actions: [proposal],
    created_at: '2099-01-01T00:00:00Z',
  }

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/auth/me' && request.method() === 'GET') return json(route, user)
    if (path === '/api/conversations' && request.method() === 'GET') return json(route, [conversation])
    if (path === `/api/conversations/${conversationId}` && request.method() === 'GET') return json(route, { conversation, messages: [message] })
    if (path === '/api/documents' && request.method() === 'GET') return json(route, { documents: [] })
    if (path === '/api/matters' && request.method() === 'GET') return json(route, [])
    if (path === '/api/mcp/source-health' && request.method() === 'GET') return json(route, { available: false, status: 'unavailable', sources: [], partitions: [] })
    if (path === '/api/tasks' && request.method() === 'GET') return json(route, { items: [currentTask], total: 1 })
    if (path === '/api/tasks/overdue' && request.method() === 'GET') return json(route, { items: [], total: 0 })
    if (path === '/api/tasks/board/telemetry' && request.method() === 'POST') return json(route, { accepted: true })
    if (path === `/api/tasks/${taskId}` && request.method() === 'GET') return json(route, currentTask)
    if (path === `/api/tasks/${taskId}/transition` && request.method() === 'POST') {
      transitions += 1
      currentTask = { ...currentTask, status: 'in_progress', version: 4 }
      return json(route, currentTask)
    }
    if (path === '/api/tasks/board/config' && request.method() === 'GET') return json(route, { statuses: [] })
    if (path === '/api/tasks/board' && request.method() === 'GET') {
      const boardTask = currentTask.status === 'in_progress' ? currentTask : task
      return json(route, {
        scope: 'mine',
        generated_at: '2099-01-01T00:00:00Z',
        risk_counts: { overdue: 0, due_today: 0, unassigned: 0, waiting_follow_up_due: 0 },
        columns: [
          ['pending', 'To Do'], ['in_progress', 'In Progress'], ['waiting', 'Waiting'],
          ['review', 'Review'], ['completed', 'Done'],
        ].map(([status, label]) => ({ status, label, total: boardTask.status === status ? 1 : 0, items: boardTask.status === status ? [boardTask] : [], next_cursor: null })),
      })
    }
    if (path === `/api/tasks/${taskId}` && request.method() === 'PATCH') {
      expect(request.postDataJSON()).toEqual({ status: 'completed' })
      completions += 1
      currentTask = { ...currentTask, status: 'completed', version: 5 }
      return json(route, currentTask)
    }
    return json(route, [])
  })

  await page.goto(`/chat?conv=${conversationId}`)
  await expect(page.getByTitle('Rename conversation')).toHaveText('Synthetic source review')
  await dismissReleaseAnnouncement(page)
  await expect(page.getByTestId('action-proposal').getByText(source.label, { exact: true })).toBeVisible()
  await expect(page.getByText('Proposed for your approval')).toBeVisible()
  await expect(page.getByText('Approving moves this task into active work. Nothing is sent.')).toBeVisible()

  expect(transitions).toBe(0)
  await page.getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByTestId('action-proposal').getByRole('status')).toContainText(
    'Approved and moved into active work.',
  )

  expect(transitions).toBe(1)
  await page.goto('/tasks')
  await page.getByRole('button', { name: 'Board', exact: true }).click()
  await page.getByLabel('Work stage', { exact: true }).selectOption('in_progress')
  await expect(page.getByText('Review the synthetic engagement letter').filter({ visible: true })).toBeVisible()
  await expect(page.getByLabel('Work stage', { exact: true })).toHaveValue('in_progress')
  await page.getByRole('button', { name: 'List', exact: true }).click()
  await page.getByRole('button', { name: `Complete task: ${task.title}`, exact: true }).click()
  await page.getByText('Closed (1)', { exact: true }).click()
  await expect(page.getByRole('button', { name: `Reopen task: ${task.title}`, exact: true })).toBeVisible()
  expect(completions).toBe(1)
  expect(currentTask.status).toBe('completed')

})
