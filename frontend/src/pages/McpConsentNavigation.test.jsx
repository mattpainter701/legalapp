import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import WorkspaceMcpAuthorizePage from './WorkspaceMcpAuthorizePage'
import ResearchMcpAuthorizePage from './ResearchMcpAuthorizePage'

const api = vi.hoisted(() => ({
  getWorkspace: vi.fn(), decideWorkspace: vi.fn(),
  getResearch: vi.fn(), decideResearch: vi.fn(),
  navigate: vi.fn(), requestId: 'first',
}))
vi.mock('../api', () => ({
  getWorkspaceMcpAuthorizationRequest: api.getWorkspace,
  decideWorkspaceMcpAuthorizationRequest: api.decideWorkspace,
  getResearchMcpAuthorizationRequest: api.getResearch,
  decideResearchMcpAuthorizationRequest: api.decideResearch,
}))
vi.mock('react-router-dom', () => ({
  useNavigate: () => api.navigate,
  useSearchParams: () => [new URLSearchParams(api.requestId ? { request_id: api.requestId } : {})],
}))

function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

beforeEach(() => { vi.resetAllMocks(); api.requestId = 'first' })
afterEach(cleanup)

describe.each([
  ['Workspace', WorkspaceMcpAuthorizePage, api.getWorkspace, api.decideWorkspace],
  ['Research', ResearchMcpAuthorizePage, api.getResearch, api.decideResearch],
])('%s consent navigation', (_name, Page, getRequest, decideRequest) => {
  it('removes the previous application and approval controls while the new request loads', async () => {
    const second = deferred()
    getRequest.mockResolvedValueOnce({ client_name: 'First application' }).mockReturnValueOnce(second.promise)
    decideRequest.mockResolvedValue({})
    const view = render(<Page />)
    expect(await screen.findByText('First application')).toBeInTheDocument()
    api.requestId = 'second'
    view.rerender(<Page />)
    expect(screen.queryByText('First application')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument()
    await act(async () => second.resolve({ client_name: 'Second application' }))
    fireEvent.click(screen.getByRole('button', { name: /approve/i }))
    expect(decideRequest).toHaveBeenCalledWith('second', true)
  })

  it.each(['success', 'failure'])('ignores a late %s from a former request', async (outcome) => {
    const first = deferred()
    getRequest.mockReturnValueOnce(first.promise).mockResolvedValueOnce({ client_name: 'Current application' })
    const view = render(<Page />)
    api.requestId = 'second'
    view.rerender(<Page />)
    expect(await screen.findByText('Current application')).toBeInTheDocument()
    await act(async () => {
      if (outcome === 'success') first.resolve({ client_name: 'Stale application' })
      else first.reject(new Error('Expired old request'))
    })
    expect(screen.getByText('Current application')).toBeInTheDocument()
    expect(screen.queryByText('Stale application')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('clears an old error when moving to a valid request and fails closed on a missing id', async () => {
    getRequest.mockRejectedValueOnce(new Error('Expired')).mockResolvedValueOnce({ client_name: 'Valid application' })
    const view = render(<Page />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    api.requestId = 'second'
    view.rerender(<Page />)
    expect(await screen.findByText('Valid application')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    api.requestId = null
    view.rerender(<Page />)
    expect(screen.getByRole('alert')).toHaveTextContent('missing its request ID')
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument()
    expect(getRequest).toHaveBeenCalledTimes(2)
  })

  it('does not redirect away from a new consent screen when an earlier decision completes', async () => {
    const decision = deferred()
    getRequest.mockResolvedValue({ client_name: 'Approved client' })
    decideRequest.mockReturnValueOnce(decision.promise)
    const view = render(<Page />)
    await screen.findByText('Approved client')
    fireEvent.click(screen.getByRole('button', { name: /approve/i }))
    expect(screen.getByRole('button', { name: /recording/i })).toBeDisabled()
    api.requestId = 'second'
    view.rerender(<Page />)
    await screen.findByText('Approved client')
    await act(async () => decision.resolve({}))
    expect(api.navigate).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /approve/i })).toBeEnabled()
  })
})
