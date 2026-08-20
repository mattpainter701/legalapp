import { expect, test } from '@playwright/test'

const user = {
  id: 'demo-user-1',
  email: 'demo@example.test',
  full_name: 'Demo Reviewer',
  role: 'admin',
  default_route: '/matters',
  enabled_modules: ['chat', 'matters', 'tasks', 'documents'],
  demo: {
    session_id: 'demo-session-1',
    used: 2,
    quota: 20,
    expires_at: '2099-01-01T00:00:00Z',
  },
}

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

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

test.describe('/demo customer entry', () => {
  test('shows required fields, API errors, and successful synthetic session disclosures', async ({ page }) => {
    let sessionCreated = false
    await page.route('**/api/**', async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      if (path === '/api/auth/me' && request.method() === 'GET') {
        return sessionCreated ? json(route, user) : json(route, { detail: 'Not authenticated' }, 401)
      }
      if (path === '/api/demo/session' && request.method() === 'POST') {
        const payload = request.postDataJSON()
        if (payload.access_code !== 'valid-demo-code') {
          return json(route, { detail: 'The demo access code is invalid.' }, 403)
        }
        sessionCreated = true
        return json(route, { session_id: user.demo.session_id })
      }
      return route.continue()
    })

    await page.goto('/demo')
    await expect(page).toHaveTitle('Guided demo | LawHand')
    await expect(page.getByRole('heading', { name: 'Start a guided demo' })).toBeVisible()
    await expect(page.getByText('populated, synthetic firm workspace')).toBeVisible()
    await expect(page.getByText('Premium AI and live integrations are disabled.')).toBeVisible()

    const submit = page.getByRole('button', { name: 'Enter demo workspace' })
    await submit.click()
    await expect(page.getByLabel('Your name')).toHaveAttribute('required', '')
    await expect(page.getByLabel('Work email')).toHaveAttribute('required', '')
    await expect(page.getByLabel('Demo access code')).toHaveAttribute('required', '')

    await page.getByLabel('Your name').fill('Demo Reviewer')
    await page.getByLabel('Work email').fill('demo@example.test')
    await page.getByLabel('Demo access code').fill('wrong-code')
    await submit.click()
    await expect(page.getByRole('alert')).toHaveText('The demo access code is invalid.')

    await page.getByLabel('Demo access code').fill('valid-demo-code')
    await submit.click()
    await expect(page).toHaveURL(/\/matters$/)
    await expect(page.getByText(/Demo session — 2 of 20 AI operations used/)).toBeVisible()
    await expect(page.getByText(/Premium AI and live integrations are disabled/)).toBeVisible()
  })
})

test('chat citation leads to review proposal approval and matching task-board status', async ({ page }) => {
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
    if (path === `/api/tasks/${taskId}` && request.method() === 'GET') return json(route, currentTask)
    if (path === `/api/tasks/${taskId}/transition` && request.method() === 'POST') {
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
    return route.continue()
  })

  await page.goto(`/chat?conv=${conversationId}`)
  await expect(page.getByTitle('Rename conversation')).toHaveText('Synthetic source review')
  await expect(page.getByTestId('action-proposal').getByText(source.label, { exact: true })).toBeVisible()
  await expect(page.getByText('Proposed for your approval')).toBeVisible()
  await expect(page.getByText('Approving moves this task into active work. Nothing is sent.')).toBeVisible()

  await page.getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByTestId('action-proposal').getByRole('status')).toContainText(
    'Approved and moved into active work.',
  )

  await page.goto('/tasks')
  await page.getByRole('button', { name: 'Board', exact: true }).click()
  await expect(page.getByLabel('Legal work board')).toBeVisible()
  await expect(page.getByText('Review the synthetic engagement letter')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'In Progress', exact: true })).toBeVisible()
})
