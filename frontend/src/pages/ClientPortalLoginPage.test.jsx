import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ClientPortalLoginPage from './ClientPortalLoginPage'
import { requestClientPortalCode, selectClientPortalMatter, verifyClientPortalCode } from '../api'

vi.mock('../api', () => ({
  requestClientPortalCode: vi.fn(),
  verifyClientPortalCode: vi.fn(),
  selectClientPortalMatter: vi.fn(),
}))

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
  requestClientPortalCode.mockResolvedValue({ message: 'sent' })
  verifyClientPortalCode.mockResolvedValue({ matter_id: 'm1', matter_name: 'Rivera', email: 'client@example.com' })
  selectClientPortalMatter.mockResolvedValue({ matter_id: 'm2', matter_name: 'Alpha' })
})

afterEach(cleanup)

it('emails a code and signs in with it', async () => {
  const user = userEvent.setup()
  renderPage()
  await user.type(screen.getByLabelText('Email'), 'client@example.com')
  await user.click(screen.getByRole('button', { name: 'Email me a sign-in code' }))
  expect(requestClientPortalCode).toHaveBeenCalledWith('client@example.com')

  const codeInput = await screen.findByLabelText(/Sign-in code/)
  await user.type(codeInput, '123456')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
  await screen.findByText('Portal home')
  expect(verifyClientPortalCode).toHaveBeenCalledWith('client@example.com', '123456')
})

it('lets the client pick a matter when the account has several', async () => {
  const user = userEvent.setup()
  verifyClientPortalCode.mockRejectedValueOnce({
    response: {
      status: 409,
      data: { detail: { code: 'multiple_matters', ticket: 't1', matters: [
        { matter_id: 'm1', matter_name: 'Rivera v. Northline', matter_number: 'RIV0001', firm_name: 'Northline & Associates' },
        { matter_id: 'm2', matter_name: 'Alpha v. Beta', matter_number: 'ALP0002', firm_name: 'Northline & Associates' },
      ] } },
    },
  })
  renderPage()
  await user.type(screen.getByLabelText('Email'), 'client@example.com')
  await user.click(screen.getByRole('button', { name: 'Email me a sign-in code' }))
  await user.type(await screen.findByLabelText(/Sign-in code/), '123456')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))

  await user.click(await screen.findByRole('button', { name: /Alpha v\. Beta/ }))
  await screen.findByText('Portal home')
  expect(selectClientPortalMatter).toHaveBeenCalledWith('t1', 'm2')
})

it('reports an incorrect code without signing in', async () => {
  const user = userEvent.setup()
  verifyClientPortalCode.mockRejectedValue({
    response: { status: 400, data: { detail: 'That code is incorrect or has expired. Request a new one.' } },
  })
  renderPage()
  await user.type(screen.getByLabelText('Email'), 'client@example.com')
  await user.click(screen.getByRole('button', { name: 'Email me a sign-in code' }))
  await user.type(await screen.findByLabelText(/Sign-in code/), '999999')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))

  expect(await screen.findByRole('alert')).toHaveTextContent(/incorrect or has expired/i)
  expect(screen.queryByText('Portal home')).not.toBeInTheDocument()
})
