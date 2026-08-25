import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { getRequest, decideRequest, navigate, searchParams } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  decideRequest: vi.fn(),
  navigate: vi.fn(),
  searchParams: vi.fn(() => [new URLSearchParams('request_id=req-123')]),
}))

vi.mock('../api', () => ({
  getResearchMcpAuthorizationRequest: getRequest,
  decideResearchMcpAuthorizationRequest: decideRequest,
}))
vi.mock('../App', () => ({ useAuth: () => ({ user: { id: 'user-1' } }) }))
vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
  useSearchParams: searchParams,
}))

import ResearchMcpAuthorizePage from './ResearchMcpAuthorizePage'

beforeEach(() => {
  vi.clearAllMocks()
  searchParams.mockReturnValue([new URLSearchParams('request_id=req-123')])
  getRequest.mockResolvedValue({
    client_name: 'Research client',
    tenant_name: 'Acme Legal',
    user_name: 'A. Attorney',
    scopes: ['research:read'],
  })
  decideRequest.mockResolvedValue({})
})

describe('Research MCP OAuth consent', () => {
  it('shows the research-only scope and records approval through the research endpoint helper', async () => {
    render(<ResearchMcpAuthorizePage />)

    expect(await screen.findByText('Research client')).toBeInTheDocument()
    expect(screen.getByText('Acme Legal')).toBeInTheDocument()
    expect(screen.getByText(/research:read/)).toBeInTheDocument()
    expect(screen.getByText(/cannot access workspace matters, documents, tasks/i)).toBeInTheDocument()
    expect(screen.getByText(/PAYG metering/i)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /approve research access/i }))
    await waitFor(() => expect(decideRequest).toHaveBeenCalledWith('req-123', true))
  })

  it('fails closed when the OAuth request id is missing', async () => {
    // The page's request-id handling is covered by the shared OAuth route contract;
    // this test keeps the consent surface from rendering an anonymous approval.
    searchParams.mockReturnValue([new URLSearchParams()])
    render(<ResearchMcpAuthorizePage />)
    expect(screen.getByRole('alert')).toHaveTextContent(/missing its request ID/i)
    expect(getRequest).not.toHaveBeenCalled()
  })
})
