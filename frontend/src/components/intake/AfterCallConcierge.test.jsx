import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AfterCallConcierge from './AfterCallConcierge'
import {
  approveLeadEngagementPacket,
  createLeadEngagementPacket,
  getLeadEngagementPacket,
  getTemplates,
  prepareLeadFollowThrough,
  previewLeadEngagementPacket,
  searchUsers,
  updateLeadEngagementPacket,
  updateLeadFollowThrough,
} from '../../api'

vi.mock('../../api', () => ({
  approveLeadEngagementPacket: vi.fn(),
  createLeadEngagementPacket: vi.fn(),
  getLeadEngagementPacket: vi.fn(),
  getTemplates: vi.fn(),
  prepareLeadFollowThrough: vi.fn(),
  previewLeadEngagementPacket: vi.fn(),
  searchUsers: vi.fn(),
  updateLeadEngagementPacket: vi.fn(),
  updateLeadFollowThrough: vi.fn(),
}))

const lead = { id: 'lead-1', status: 'new', contact: { display_name: 'Alex Client', email: 'alex@example.com' } }

beforeEach(() => {
  vi.clearAllMocks()
  prepareLeadFollowThrough.mockResolvedValue({
    lead_id: lead.id,
    version: 3,
    decision: null,
    suggestion: { brief: 'Call summary', missing_information: [] },
  })
  getLeadEngagementPacket.mockResolvedValue(null)
  getTemplates.mockResolvedValue({ items: [{ id: 'template-1', title: 'Approved engagement', category: 'engagement_letter', status: 'approved' }] })
  searchUsers.mockResolvedValue([{ id: 'attorney-2', full_name: 'Dana Lawyer' }])
})

afterEach(() => cleanup())

describe('AfterCallConcierge', () => {
  it('prepares the lead when opened and carries the Zoom communication id', async () => {
    const user = userEvent.setup()
    render(<AfterCallConcierge lead={lead} communicationId="call-1" enabled />)

    await user.click(screen.getByRole('button', { name: /after-call concierge/i }))
    expect(await screen.findByText('Attorney-ready handoff')).toBeInTheDocument()
    await waitFor(() => expect(prepareLeadFollowThrough).toHaveBeenCalledWith('lead-1', { communication_id: 'call-1' }))
    expect(getLeadEngagementPacket).toHaveBeenCalledWith('lead-1')
  })

  it('uses the response version for decisions and sends an explicit reassignment recipient', async () => {
    const user = userEvent.setup()
    updateLeadFollowThrough.mockResolvedValue({ lead_id: lead.id, version: 4, decision: 'pursue', next_action: 'Send options' })
    render(<AfterCallConcierge lead={lead} enabled />)
    await user.click(screen.getByRole('button', { name: /after-call concierge/i }))
    await screen.findByText('Attorney-ready handoff')

    await user.click(screen.getByRole('button', { name: 'Pursue' }))
    await waitFor(() => expect(updateLeadFollowThrough).toHaveBeenCalledWith('lead-1', { decision: 'pursue', expected_version: 3 }))

    await user.click(screen.getByRole('button', { name: 'Reassign' }))
    await user.type(screen.getByRole('textbox', { name: 'Find attorney for reassignment' }), 'Dana')
    await user.click(await screen.findByRole('button', { name: 'Dana Lawyer' }))
    await waitFor(() => expect(updateLeadFollowThrough).toHaveBeenLastCalledWith('lead-1', {
      decision: 'reassign',
      assigned_attorney_user_id: 'attorney-2',
      expected_version: 4,
    }))
  })

  it('keeps the attorney picker open when reassignment is rejected', async () => {
    const user = userEvent.setup()
    updateLeadFollowThrough.mockRejectedValue({ response: { data: { detail: 'Prospect changed; refresh before updating' } } })
    render(<AfterCallConcierge lead={lead} enabled />)
    await user.click(screen.getByRole('button', { name: /after-call concierge/i }))
    await screen.findByText('Attorney-ready handoff')

    await user.click(screen.getByRole('button', { name: 'Reassign' }))
    await user.type(screen.getByRole('textbox', { name: 'Find attorney for reassignment' }), 'Dana')
    await user.click(await screen.findByRole('button', { name: 'Dana Lawyer' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Prospect changed; refresh before updating')
    expect(screen.getByRole('textbox', { name: 'Find attorney for reassignment' })).toBeInTheDocument()
  })

  it('does not expose a send action for the packet workflow', async () => {
    const user = userEvent.setup()
    render(<AfterCallConcierge lead={{ ...lead, status: 'pursue' }} enabled />)
    await user.click(screen.getByRole('button', { name: /after-call concierge/i }))
    await screen.findByText('Attorney-ready handoff')
    await user.click(screen.getByRole('button', { name: /prepare a fee agreement packet/i }))
    expect(screen.getByText('No send')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /send/i })).not.toBeInTheDocument()
    expect(approveLeadEngagementPacket).not.toHaveBeenCalled()
    expect(createLeadEngagementPacket).not.toHaveBeenCalled()
    expect(previewLeadEngagementPacket).not.toHaveBeenCalled()
    expect(updateLeadEngagementPacket).not.toHaveBeenCalled()
  })

  it('uses the narrow packet PATCH contract for an existing draft', async () => {
    const user = userEvent.setup()
    getLeadEngagementPacket.mockResolvedValue({
      id: 'packet-1',
      template_id: 'template-1',
      version: 2,
      status: 'draft',
      fields: {
        template_id: 'template-1',
        idempotency_key: 'original-key',
        fee_structure: 'Flat fee',
        fee_amount: '1000',
        scope_bullets: ['Review petition'],
        client: { name: 'Alex Client', email: 'alex@example.com' },
        attorney: { name: 'Dana Lawyer' },
        signers: [{ name: 'Alex Client', email: 'alex@example.com', role: 'client' }],
      },
    })
    updateLeadEngagementPacket.mockResolvedValue({ id: 'packet-1', version: 3, status: 'draft' })
    previewLeadEngagementPacket.mockResolvedValue({ id: 'packet-1', version: 4, status: 'previewed', preview: 'Preview' })
    render(<AfterCallConcierge lead={{ ...lead, status: 'pursue' }} enabled />)
    await user.click(screen.getByRole('button', { name: /after-call concierge/i }))
    await screen.findByText('Attorney-ready handoff')
    await user.click(screen.getByRole('button', { name: /prepare a fee agreement packet/i }))
    await user.click(screen.getByRole('button', { name: /save & render preview/i }))
    await waitFor(() => expect(updateLeadEngagementPacket).toHaveBeenCalledWith('lead-1', expect.not.objectContaining({ idempotency_key: expect.anything() })))
    expect(updateLeadEngagementPacket).toHaveBeenCalledWith('lead-1', expect.objectContaining({ expected_version: 2, fee_amount: '1000', template_id: 'template-1' }))
    expect(previewLeadEngagementPacket).toHaveBeenCalledWith('lead-1')
  })
})
