import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DemoLoginPage from './DemoLoginPage'

const mocks = vi.hoisted(() => ({
  createDemoSession: vi.fn(),
  login: vi.fn(),
}))

vi.mock('../App', () => ({ useAuth: () => ({ login: mocks.login }) }))
vi.mock('../api', () => ({ createDemoSession: mocks.createDemoSession }))

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/demo']}>
      <Routes>
        <Route path="/demo" element={<DemoLoginPage />} />
        <Route path="*" element={<div>Redirected to the workspace</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

async function fillForm(user) {
  await user.type(screen.getByLabelText(/your name/i), 'Alex Prospect')
  await user.type(screen.getByLabelText(/work email/i), 'alex@example.com')
  await user.type(screen.getByLabelText(/demo access code/i), 'demo-secret')
}

describe('DemoLoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.createDemoSession.mockResolvedValue({ session_id: 'demo-session' })
    mocks.login.mockResolvedValue({ default_route: '/matters' })
  })

  afterEach(() => cleanup())

  it('submits the prospect details and redirects to the returned workspace route', async () => {
    const user = userEvent.setup()
    renderPage()
    await fillForm(user)

    await user.click(screen.getByRole('button', { name: /enter demo workspace/i }))

    expect(mocks.createDemoSession).toHaveBeenCalledWith({
      full_name: 'Alex Prospect',
      email: 'alex@example.com',
      access_code: 'demo-secret',
    })
    expect(mocks.login).toHaveBeenCalledOnce()
    expect(await screen.findByText('Redirected to the workspace')).toBeInTheDocument()
  })

  it('shows loading state and disables submission until provisioning settles', async () => {
    let resolveRequest
    mocks.createDemoSession.mockReturnValue(new Promise((resolve) => { resolveRequest = resolve }))
    const user = userEvent.setup()
    renderPage()
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: /enter demo workspace/i }))

    expect(screen.getByRole('button', { name: /preparing your workspace/i })).toBeDisabled()
    resolveRequest({ session_id: 'demo-session' })
    expect(await screen.findByText('Redirected to the workspace')).toBeInTheDocument()
  })

  it('resumes an active workspace with email and access code only', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: 'Resume demo' }))
    expect(screen.queryByLabelText(/your name/i)).not.toBeInTheDocument()
    await user.type(screen.getByLabelText(/work email/i), 'alex@example.com')
    await user.type(screen.getByLabelText(/demo access code/i), 'demo-secret')
    await user.click(screen.getByRole('button', { name: /resume demo workspace/i }))

    expect(mocks.createDemoSession).toHaveBeenCalledWith({
      email: 'alex@example.com',
      access_code: 'demo-secret',
    })
    expect(mocks.login).toHaveBeenCalledOnce()
    expect(await screen.findByText('Redirected to the workspace')).toBeInTheDocument()
  })

  it.each([
    ['invalid access code', 'Invalid demo access code'],
    ['capacity exhaustion', 'All demo workspaces are in use'],
  ])('surfaces the backend detail for %s', async (_label, detail) => {
    mocks.createDemoSession.mockRejectedValueOnce({ response: { data: { detail }, status: 503 } })
    const user = userEvent.setup()
    renderPage()
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: /enter demo workspace/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(detail)
    expect(screen.getByRole('button', { name: /enter demo workspace/i })).toBeEnabled()
    expect(mocks.login).not.toHaveBeenCalled()
  })
})
