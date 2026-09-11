import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ClientPortalLoginPage from './ClientPortalLoginPage'
import { loginClientPortalAccount } from '../api'

vi.mock('../api', () => ({ loginClientPortalAccount: vi.fn() }))

const renderPage = () => render(
  <MemoryRouter initialEntries={['/portal/client/login']}>
    <Routes>
      <Route path="/portal/client/login" element={<ClientPortalLoginPage />} />
      <Route path="/portal/client/matter" element={<div>Portal home</div>} />
    </Routes>
  </MemoryRouter>,
)

beforeEach(() => {
  vi.resetAllMocks()
  loginClientPortalAccount.mockResolvedValue({ matter_id: 'm1', matter_name: 'Rivera', email: 'client@example.com' })
})

afterEach(cleanup)

it('signs in and opens the matter when the account has one', async () => {
  const user = userEvent.setup()
  renderPage()
  await user.type(screen.getByLabelText('Email'), 'client@example.com')
  await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
  await screen.findByText('Portal home')
  expect(loginClientPortalAccount).toHaveBeenCalledWith('client@example.com', 'correct-horse-battery', undefined)
})

it('lets the client pick a matter when several are available', async () => {
  const user = userEvent.setup()
  loginClientPortalAccount.mockRejectedValueOnce({
    response: {
      status: 409,
      data: { detail: { code: 'multiple_matters', matters: [
        { matter_id: 'm1', matter_name: 'Rivera v. Northline', matter_number: 'RIV0001' },
        { matter_id: 'm2', matter_name: 'Alpha v. Beta', matter_number: 'ALP0002' },
      ] } },
    },
  })
  loginClientPortalAccount.mockResolvedValueOnce({ matter_id: 'm2', matter_name: 'Alpha v. Beta' })
  renderPage()
  await user.type(screen.getByLabelText('Email'), 'client@example.com')
  await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))

  await user.click(await screen.findByRole('button', { name: /Alpha v\. Beta/ }))
  await screen.findByText('Portal home')
  expect(loginClientPortalAccount).toHaveBeenLastCalledWith('client@example.com', 'correct-horse-battery', 'm2')
})

it('reports invalid credentials without signing in', async () => {
  const user = userEvent.setup()
  loginClientPortalAccount.mockRejectedValue({ response: { status: 401 } })
  renderPage()
  await user.type(screen.getByLabelText('Email'), 'client@example.com')
  await user.type(screen.getByLabelText('Password'), 'wrong-password')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/do not match an account/i)
  expect(screen.queryByText('Portal home')).not.toBeInTheDocument()
})

it('explains when the account has no matter access', async () => {
  const user = userEvent.setup()
  loginClientPortalAccount.mockRejectedValue({ response: { status: 403 } })
  renderPage()
  await user.type(screen.getByLabelText('Email'), 'client@example.com')
  await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/new invitation/i)
})
