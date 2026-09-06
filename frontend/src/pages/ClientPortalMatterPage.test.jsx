import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ClientPortalMatterPage from './ClientPortalMatterPage'
import {
  getClientPortalSession,
  getClientPortalMatter,
  getClientPortalMediation,
  listClientPortalMessages,
  sendClientPortalMessage,
  markClientPortalMessagesRead,
  listClientPortalDocuments,
  listClientPortalInvoices,
  listClientPortalSignatures,
  logoutClientPortal,
} from '../api'

vi.mock('../api', () => ({
  getClientIntake: vi.fn().mockRejectedValue({ response: { status: 404 } }),
  submitClientIntake: vi.fn(),
  getClientPortalSession: vi.fn(),
  logoutClientPortal: vi.fn(),
  getClientPortalMatter: vi.fn(),
  getClientPortalMediation: vi.fn(),
  listClientPortalMessages: vi.fn(),
  sendClientPortalMessage: vi.fn(),
  markClientPortalMessagesRead: vi.fn(),
  listClientPortalDocuments: vi.fn(),
  uploadClientPortalDocument: vi.fn(),
  downloadClientPortalDocumentUrl: (id) => `/api/portal/client/documents/${id}/download`,
  listClientPortalInvoices: vi.fn(),
  downloadClientPortalInvoiceUrl: (id) => `/api/portal/client/invoices/${id}/download`,
  listClientPortalSignatures: vi.fn(),
  signClientPortalSignature: vi.fn(),
  declineClientPortalSignature: vi.fn(),
}))

const matterView = {
  matter_id: 'matter-1',
  matter_name: 'Rivera v. Northline Freight',
  status: 'open',
  stage: 'discovery',
  practice_area: 'Personal Injury',
  description: 'Rear-end collision on I-94.',
  key_date_list: [
    { label: 'Mediation', value: '2019-04-01', iso_date: '2019-04-01', is_past: true, days_away: -400 },
    { label: 'Status conference', value: '2026-09-03', iso_date: '2026-09-03', is_past: false, days_away: 10 },
    { label: 'Venue note', value: 'Cook County', iso_date: null, is_past: false, days_away: null },
  ],
  next_key_date: {
    label: 'Status conference', value: '2026-09-03', iso_date: '2026-09-03', is_past: false, days_away: 10,
  },
  attorneys: [{ name: 'Dana Reyes', role: 'lead', email: 'dana@firm.example' }],
  unread_message_count: 2,
  document_count: 4,
  pending_signature_count: 1,
  open_invoice_count: 1,
  outstanding_balance: '600.00',
}

const sessionExpired = () => Object.assign(new Error('expired'), { response: { status: 401 } })

beforeEach(() => {
  vi.clearAllMocks()
  getClientPortalSession.mockResolvedValue({
    matter_id: 'matter-1',
    matter_name: matterView.matter_name,
    email: 'client@example.com',
    expires_at: '2030-01-01T00:00:00Z',
    invite_expires_at: '2030-01-01T00:00:00Z',
  })
  getClientPortalMatter.mockResolvedValue(matterView)
  getClientPortalMediation.mockResolvedValue({ status: 404 })
  listClientPortalMessages.mockResolvedValue({ messages: [], unread_count: 0, total: 0, has_more: false })
  markClientPortalMessagesRead.mockResolvedValue({ messages_seen_at: '2026-01-01T00:00:00Z', unread_count: 0 })
  listClientPortalDocuments.mockResolvedValue([])
  listClientPortalInvoices.mockResolvedValue({
    invoices: [], total_billed: '0', total_paid: '0', outstanding_balance: '0', overdue_balance: '0',
  })
  listClientPortalSignatures.mockResolvedValue([])
  logoutClientPortal.mockResolvedValue(undefined)
})

afterEach(cleanup)

describe('ClientPortalMatterPage', () => {
  it('shows the optional read-only mediation overlay without changing the base portal', async () => {
    getClientPortalMediation.mockResolvedValue({
      mediation: { case_name: 'Rivera mediation', status: 'active', mediation_stage: 'proposal' },
      own_assets: [{ id: 'a1', description: 'Your disclosure', status: 'submitted' }],
      shared_assets: [{ id: 'a2', description: 'Shared schedule', status: 'sent' }],
      documents: [{ id: 'd1', filename: 'released.pdf', is_own: false, release_state: 'released_to_you', download_url: '/api/portal/client/mediation/documents/d1/download' }],
      proposals: [{ id: 'p1', title: 'Opening proposal', is_own: false, review_state: 'approved', release_state: 'released_to_you', released_at: '2026-08-20T00:00:00Z' }],
    })
    const user = userEvent.setup()
    render(<ClientPortalMatterPage />)

    const mediationTab = await screen.findByRole('tab', { name: 'Mediation' })
    await user.click(mediationTab)
    expect(await screen.findByText('Rivera mediation')).toBeInTheDocument()
    expect(screen.getByText('Your disclosure')).toBeInTheDocument()
    expect(screen.getByText('Shared schedule')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Download/ })).toHaveAttribute('href', '/api/portal/client/mediation/documents/d1/download')
    expect(screen.getAllByText(/Released to you/)).toHaveLength(2)
  })

  it('keeps the base portal available when the mediation add-on is unavailable', async () => {
    getClientPortalMediation.mockRejectedValue({ response: { status: 404 } })
    render(<ClientPortalMatterPage />)

    expect(await screen.findByText('Unread messages')).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Mediation' })).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')
  })

  it('does not hide an expired session as an unavailable mediation add-on', async () => {
    getClientPortalMediation.mockRejectedValue(sessionExpired())
    render(<ClientPortalMatterPage />)

    expect(await screen.findByText("You've been signed out")).toBeInTheDocument()
  })

  it('lands on a summary of what is waiting on the client', async () => {
    render(<ClientPortalMatterPage />)

    expect(await screen.findByText('Rivera v. Northline Freight')).toBeInTheDocument()
    expect(screen.getByText('Unread messages')).toBeInTheDocument()
    expect(screen.getByText('Balance due')).toBeInTheDocument()
    expect(screen.getByText('$600.00')).toBeInTheDocument()

    // The next date is called out on its own, with plain-language timing.
    expect(screen.getByText('Next key date')).toBeInTheDocument()
    expect(screen.getAllByText('in 10 days').length).toBeGreaterThan(0)

    expect(screen.getByText('dana@firm.example')).toBeInTheDocument()
  })

  it('badges the tabs that need attention', async () => {
    render(<ClientPortalMatterPage />)

    const messagesTab = await screen.findByRole('tab', { name: /Messages/ })
    expect(within(messagesTab).getByText('2')).toBeInTheDocument()
    expect(messagesTab).toHaveAttribute('aria-selected', 'false')

    const signaturesTab = screen.getByRole('tab', { name: /Signatures/ })
    expect(within(signaturesTab).getByText('1')).toBeInTheDocument()

    // Documents has nothing outstanding, so it carries no badge.
    const documentsTab = screen.getByRole('tab', { name: /Documents/ })
    expect(within(documentsTab).queryByText('4')).not.toBeInTheDocument()
  })

  it('marks firm messages read when the client opens the thread', async () => {
    listClientPortalMessages.mockResolvedValue({
      messages: [
        {
          id: 'm1',
          direction: 'outbound',
          subject: 'Update',
          body: 'We filed the motion.',
          occurred_at: '2026-08-20T12:00:00Z',
          unread: true,
        },
      ],
      unread_count: 1,
      total: 1,
      has_more: false,
    })
    const user = userEvent.setup()
    render(<ClientPortalMatterPage />)

    await user.click(await screen.findByRole('tab', { name: /Messages/ }))
    expect(await screen.findByText('We filed the motion.')).toBeInTheDocument()
    await waitFor(() => expect(markClientPortalMessagesRead).toHaveBeenCalledTimes(1))
  })

  it('sends a trimmed message and refreshes the thread', async () => {
    sendClientPortalMessage.mockResolvedValue({ id: 'm2', direction: 'inbound' })
    const user = userEvent.setup()
    render(<ClientPortalMatterPage />)

    await user.click(await screen.findByRole('tab', { name: /Messages/ }))
    const box = await screen.findByLabelText('Message to your legal team')
    await user.type(box, '   Any update?   ')
    await user.click(screen.getByRole('button', { name: /Send/ }))

    await waitFor(() =>
      expect(sendClientPortalMessage).toHaveBeenCalledWith({ body: 'Any update?' }),
    )
  })

  it('does not send an empty message', async () => {
    const user = userEvent.setup()
    render(<ClientPortalMatterPage />)

    await user.click(await screen.findByRole('tab', { name: /Messages/ }))
    const send = await screen.findByRole('button', { name: /Send/ })
    expect(send).toBeDisabled()
    await user.type(await screen.findByLabelText('Message to your legal team'), '   ')
    expect(send).toBeDisabled()
    expect(sendClientPortalMessage).not.toHaveBeenCalled()
  })

  it('shows an invoice balance with an overdue marker and a pay link', async () => {
    listClientPortalInvoices.mockResolvedValue({
      invoices: [
        {
          id: 'i1',
          invoice_number: 'INV-001',
          status: 'partially_paid',
          issue_date: '2026-06-01',
          due_date: '2026-07-01',
          total: '1000.00',
          amount_paid: '400.00',
          balance_due: '600.00',
          is_overdue: true,
          days_overdue: 10,
          stripe_payment_link: 'https://pay.example/inv-001',
        },
      ],
      total_billed: '1000.00',
      total_paid: '400.00',
      outstanding_balance: '600.00',
      overdue_balance: '600.00',
    })
    const user = userEvent.setup()
    render(<ClientPortalMatterPage />)

    await user.click(await screen.findByRole('tab', { name: /Invoices/ }))
    expect(await screen.findByText('INV-001')).toBeInTheDocument()
    expect(screen.getByText('10d overdue')).toBeInTheDocument()
    expect(screen.getByText('$400.00 of $1,000.00 paid')).toBeInTheDocument()
    expect(screen.getByText('Balance due (overdue)')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Pay now' })).toHaveAttribute(
      'href',
      'https://pay.example/inv-001',
    )
    expect(screen.getByRole('link', { name: 'PDF' })).toHaveAttribute(
      'href',
      '/api/portal/client/invoices/i1/download',
    )
  })

  it('separates documents the firm shared from ones the client sent', async () => {
    listClientPortalDocuments.mockResolvedValue([
      { id: 'd1', filename: 'settlement.pdf', file_size: 2048, uploaded_by_client: false, created_at: '2026-08-01T00:00:00Z' },
      { id: 'd2', filename: 'receipt.jpg', file_size: 1024, uploaded_by_client: true, created_at: '2026-08-02T00:00:00Z', description: 'Tow receipt' },
    ])
    const user = userEvent.setup()
    render(<ClientPortalMatterPage />)

    await user.click(await screen.findByRole('tab', { name: /Documents/ }))
    expect(await screen.findByText('Shared by your legal team')).toBeInTheDocument()
    expect(screen.getByText('Sent by you')).toBeInTheDocument()
    expect(screen.getByText('Tow receipt')).toBeInTheDocument()
  })

  it('explains what to do when the session has expired', async () => {
    getClientPortalMatter.mockRejectedValue(sessionExpired())
    render(<ClientPortalMatterPage />)

    expect(await screen.findByText("You've been signed out")).toBeInTheDocument()
    expect(screen.getByText(/invitation email/)).toBeInTheDocument()
  })

  it('escalates a tab-level expiry to the whole page', async () => {
    listClientPortalInvoices.mockRejectedValue(sessionExpired())
    const user = userEvent.setup()
    render(<ClientPortalMatterPage />)

    await user.click(await screen.findByRole('tab', { name: /Invoices/ }))
    expect(await screen.findByText("You've been signed out")).toBeInTheDocument()
  })

  it('signs the client out and ends the session', async () => {
    const user = userEvent.setup()
    render(<ClientPortalMatterPage />)

    await user.click(await screen.findByRole('button', { name: /Sign out/ }))
    await waitFor(() => expect(logoutClientPortal).toHaveBeenCalledTimes(1))
    expect(await screen.findByText("You've been signed out")).toBeInTheDocument()
  })

  it('still signs the client out when the logout call fails', async () => {
    logoutClientPortal.mockRejectedValue(new Error('network down'))
    const user = userEvent.setup()
    render(<ClientPortalMatterPage />)

    await user.click(await screen.findByRole('button', { name: /Sign out/ }))
    expect(await screen.findByText("You've been signed out")).toBeInTheDocument()
  })

  it('offers a retry when the matter fails to load for a non-auth reason', async () => {
    getClientPortalMatter.mockRejectedValueOnce(new Error('boom'))
    const user = userEvent.setup()
    render(<ClientPortalMatterPage />)

    expect(await screen.findByText('Something went wrong')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Rivera v. Northline Freight')).toBeInTheDocument()
  })
})
