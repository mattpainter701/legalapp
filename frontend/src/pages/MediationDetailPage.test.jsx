import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MediationDetailPage from './MediationDetailPage'
import {
  getMediationCase, listMediationParties, listMediationAssets,
  listMediationDocuments, listMediationProposals, reviewMediationProposal,
} from '../api'

const authHarness = vi.hoisted(() => ({
  user: { capabilities: ['approve_legal_work'] },
}))

vi.mock('../App', () => ({
  useAuth: () => authHarness,
}))

vi.mock('react-router-dom', () => ({
  useParams: () => ({ id: 'case-1' }),
  useNavigate: () => vi.fn(),
}))

vi.mock('../components/dialog/ConfirmProvider', () => ({
  useConfirm: () => vi.fn().mockResolvedValue(true),
}))

vi.mock('../components/toast/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}))

vi.mock('../api', () => ({
  getMediationCase: vi.fn(), updateMediationCase: vi.fn(), advanceMediationCase: vi.fn(),
  addMediationEvent: vi.fn(), deleteMediationCase: vi.fn(),
  listMediationParties: vi.fn(), createMediationParty: vi.fn(), updateMediationParty: vi.fn(),
  deleteMediationParty: vi.fn(), inviteMediationParty: vi.fn(),
  listMediationAssets: vi.fn(), createMediationAsset: vi.fn(), updateMediationAsset: vi.fn(),
  deleteMediationAsset: vi.fn(), approveMediationAsset: vi.fn(), sendMediationAsset: vi.fn(),
  listMediationDocuments: vi.fn(), uploadMediationDocument: vi.fn(),
  downloadMediationDocumentUrl: vi.fn(), releaseMediationDocument: vi.fn(),
  listMediationProposals: vi.fn(), createMediationProposal: vi.fn(),
  reviewMediationProposal: vi.fn(), releaseMediationProposal: vi.fn(),
}))

const mediation = {
  id: 'case-1', case_name: 'Doe v. Doe', title: 'Doe v. Doe', party_a: 'Jane Doe',
  party_b: 'John Doe', status: 'active', mediation_stage: 'Negotiation',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
}
const parties = [
  { id: 'party-a', name: 'Jane Doe', role: 'our_client', email: 'jane@example.com' },
  { id: 'party-b', name: 'John Doe', role: 'opposing_party', email: 'john@example.com' },
]

beforeEach(() => {
  vi.clearAllMocks()
  authHarness.user.capabilities = ['approve_legal_work']
  getMediationCase.mockResolvedValue({ mediation, sessions: [] })
  listMediationParties.mockResolvedValue(parties)
  listMediationAssets.mockResolvedValue([])
  listMediationDocuments.mockResolvedValue([])
  listMediationProposals.mockResolvedValue([])
  reviewMediationProposal.mockResolvedValue({})
})

afterEach(cleanup)

describe('MediationDetailPage review and release controls', () => {
  it('loads parties with the case and excludes the uploader from document release candidates', async () => {
    listMediationDocuments.mockResolvedValue([{
      id: 'doc-1', filename: 'private.pdf', description: 'Private statement',
      uploaded_by_party_id: 'party-a', is_released: false, recipient_party_ids: [],
      content_type: 'application/pdf', file_size: 100, created_at: '2026-01-01T00:00:00Z',
    }])
    const user = userEvent.setup()
    render(<MediationDetailPage />)

    expect((await screen.findAllByText('Doe v. Doe')).length).toBeGreaterThan(0)
    await user.click(screen.getByRole('tab', { name: 'Parties' }))
    expect(await screen.findByText('Jane Doe')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Documents' }))
    expect(await screen.findByText('private.pdf')).toBeInTheDocument()
    expect(screen.getByText('Firm / uploader only')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Release' }))
    const dialog = await screen.findByRole('dialog', { name: /Release document/i })
    expect(within(dialog).getByText('John Doe')).toBeInTheDocument()
    expect(within(dialog).queryByText('Jane Doe')).not.toBeInTheDocument()
  })

  it('approves a pending proposal and then exposes the release control', async () => {
    const proposal = {
      id: 'proposal-1', title: 'Initial offer', body: '60/40 split',
      proposed_by_party_id: 'party-a', proposed_by_name: 'Jane Doe',
      review_state: 'pending', is_released: false, recipient_party_ids: [],
      status: 'open', created_at: '2026-01-01T00:00:00Z',
    }
    listMediationProposals.mockImplementation(async () => [proposal])
    reviewMediationProposal.mockImplementation(async () => {
      proposal.review_state = 'approved'
      return proposal
    })
    const user = userEvent.setup()
    render(<MediationDetailPage />)
    await screen.findAllByText('Doe v. Doe')
    await user.click(screen.getByRole('tab', { name: 'Proposals' }))
    expect(await screen.findByText('Initial offer')).toBeInTheDocument()
    expect(screen.getByText('pending')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(reviewMediationProposal).toHaveBeenCalledWith('case-1', 'proposal-1', 'approved'))
    expect(await screen.findByRole('button', { name: 'Release' })).toBeInTheDocument()
  })

  it('lets staff prepare records without exposing attorney approval or release actions', async () => {
    authHarness.user.capabilities = ['manage_documents', 'manage_matters']
    listMediationDocuments.mockResolvedValue([{
      id: 'doc-1', filename: 'private.pdf', description: 'Private statement',
      uploaded_by_party_id: 'party-a', is_released: false, recipient_party_ids: [],
      content_type: 'application/pdf', file_size: 100, created_at: '2026-01-01T00:00:00Z',
    }])
    listMediationProposals.mockResolvedValue([{
      id: 'proposal-1', title: 'Initial offer', body: '60/40 split',
      proposed_by_party_id: 'party-a', proposed_by_name: 'Jane Doe',
      review_state: 'pending', is_released: false, recipient_party_ids: [],
      status: 'open', created_at: '2026-01-01T00:00:00Z',
    }])
    const user = userEvent.setup()
    render(<MediationDetailPage />)

    expect(await screen.findByText(/authorized attorney must approve/i)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Documents' }))
    expect(await screen.findByText('private.pdf')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Release' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Proposals' }))
    expect(await screen.findByText('Initial offer')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Changes' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument()
  })
})
