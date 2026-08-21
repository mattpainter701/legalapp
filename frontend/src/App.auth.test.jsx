import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getMe: vi.fn(),
}))

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal()),
  getMe: apiMocks.getMe,
}))

import { AuthProvider, useAuth } from './App'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, reject, resolve }
}

function AuthProbe() {
  const { user, login, logout, loading } = useAuth()
  return (
    <>
      <output aria-label="Current user">{user?.email || 'signed-out'}</output>
      <output aria-label="Auth loading">{String(loading)}</output>
      <button type="button" onClick={() => void login()}>Resolve login</button>
      <button type="button" onClick={logout}>Log out locally</button>
    </>
  )
}

describe('AuthProvider request ordering', () => {
  beforeEach(() => {
    apiMocks.getMe.mockReset()
  })

  afterEach(() => cleanup())

  it('does not let a stale bootstrap failure erase a newer login', async () => {
    const bootstrap = deferred()
    const authenticatedUser = { id: 'user-1', email: 'attorney@example.com' }
    apiMocks.getMe
      .mockImplementationOnce(() => bootstrap.promise)
      .mockResolvedValueOnce(authenticatedUser)

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    )

    await waitFor(() => expect(apiMocks.getMe).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: 'Resolve login' }))
    expect(await screen.findByText(authenticatedUser.email)).toBeInTheDocument()

    await act(async () => {
      bootstrap.reject(new Error('anonymous refresh failed after login'))
      await Promise.resolve()
    })

    expect(screen.getByLabelText('Current user')).toHaveTextContent(authenticatedUser.email)
    expect(screen.getByLabelText('Auth loading')).toHaveTextContent('false')
  })

  it('invalidates pending probes when the user logs out', async () => {
    const pendingProbe = deferred()
    apiMocks.getMe.mockImplementationOnce(() => pendingProbe.promise)

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    )

    await waitFor(() => expect(apiMocks.getMe).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: 'Log out locally' }))

    await act(async () => {
      pendingProbe.resolve({ id: 'user-1', email: 'attorney@example.com' })
      await pendingProbe.promise
    })

    expect(screen.getByLabelText('Current user')).toHaveTextContent('signed-out')
    expect(screen.getByLabelText('Auth loading')).toHaveTextContent('false')
  })
})
