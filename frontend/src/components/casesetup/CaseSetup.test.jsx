import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CaseSetupCard from './CaseSetupCard'
import PaperworkDrawer from './PaperworkDrawer'
import { dueDateToIso, paperworkOptions } from './paperwork'
import api, { getAdminUsers, getMatterDocuments, getMatterPaperwork, matterPaperworkAction } from '../../api'

vi.mock('../../api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  getMatterPaperwork: vi.fn(), matterPaperworkAction: vi.fn(), getMatterDocuments: vi.fn(),
  getAdminUsers: vi.fn(), getContacts: vi.fn(), getIntakeStarterPack: vi.fn(),
}))

afterEach(cleanup)

const packet = (overrides = {}) => ({
  id: 'packet', matter_id: 'matter', status: 'awaiting_documents',
  requirements: { fee_agreement: { completed: false }, questionnaire: { completed: false } },
  questions: [], answers: {}, delivery: {}, sent_at: '2026-09-01T12:00:00Z',
  completed_at: null, meeting: null, timezone: 'America/Chicago', ...overrides,
})

beforeEach(() => {
  vi.resetAllMocks()
  getMatterPaperwork.mockResolvedValue(packet())
  matterPaperworkAction.mockResolvedValue(packet())
  getMatterDocuments.mockResolvedValue([])
  getAdminUsers.mockResolvedValue([])
  api.post.mockResolvedValue({ data: packet() })
})

it('states a due date at 5pm in the client timezone, not in UTC', () => {
  // A naive date would chase a Chicago client at 7pm local on the day before.
  expect(dueDateToIso('2026-09-15', 'America/Chicago')).toBe('2026-09-15T22:00:00.000Z')
  expect(dueDateToIso('2026-09-15', 'UTC')).toBe('2026-09-15T17:00:00.000Z')
  expect(dueDateToIso('', 'UTC')).toBeNull()
})

it('carries a due date for each dated item into the intake request', () => {
  const options = paperworkOptions({
    email: 'jane@example.com', channels: ['email'], smsPermissionVerified: false, ownerId: '',
    agreementDocumentId: 'agreement', agreementDue: '2026-09-15',
    forms: [{ documentId: 'form', label: 'Retainer addendum', requiresSignature: true, due: '2026-09-20' }],
    includeQuestionnaire: true, questionnaireDue: '2026-09-18', questions: 'Describe the matter.',
    uploads: 'Marriage certificate', uploadsDue: '2026-09-25', portalAfterSigning: true,
  }, 'UTC')
  expect(options.agreement_due_at).toBe('2026-09-15T17:00:00.000Z')
  expect(options.selected_documents[0]).toMatchObject({ document_id: 'form', due_at: '2026-09-20T17:00:00.000Z' })
  expect(options.questionnaire_due_at).toBe('2026-09-18T17:00:00.000Z')
  expect(options.upload_requirements[0]).toMatchObject({ key: 'upload_1', due_at: '2026-09-25T17:00:00.000Z' })
})

it('drops a questionnaire due date when the questionnaire is not included', () => {
  const options = paperworkOptions({
    email: 'jane@example.com', channels: ['email'], forms: [], uploads: '',
    includeQuestionnaire: false, questionnaireDue: '2026-09-18', questions: 'Unused',
  }, 'UTC')
  expect(options.questionnaire_due_at).toBeNull()
  expect(options.questions).toEqual([])
})

it('marks a passed deadline overdue on the paperwork strip', async () => {
  getMatterPaperwork.mockResolvedValue(packet({
    requirements: {
      fee_agreement: { completed: false, due_at: '2020-01-01T17:00:00Z' },
      questionnaire: { completed: true, completed_at: '2026-09-02T12:00:00Z', due_at: '2030-01-01T17:00:00Z' },
    },
  }))
  render(<CaseSetupCard matterId="matter" />)
  expect(await screen.findByText(/Overdue/)).toBeInTheDocument()
  // A completed requirement is never chased, whatever its date said.
  expect(screen.queryByText(/Due Jan 1, 2030/)).not.toBeInTheDocument()
  expect(screen.getByText('1 of 2 complete · sent 9/1/2026')).toBeInTheDocument()
})

it('offers the kickoff when the matter has no packet yet', async () => {
  getMatterPaperwork.mockRejectedValue({ response: { status: 404 } })
  render(<CaseSetupCard matterId="matter" />)
  expect(await screen.findByRole('button', { name: 'Send client paperwork' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Start this case' })).toBeInTheDocument()
})

it('sends the chosen documents and their deadlines from the drawer', async () => {
  const user = userEvent.setup()
  const onSent = vi.fn()
  render(<PaperworkDrawer
    matterId="matter"
    documents={[
      { id: 'agreement', filename: 'Fee agreement.pdf', content_type: 'application/pdf' },
      { id: 'form', filename: 'Intake form.pdf', content_type: 'application/pdf' },
    ]}
    clientEmail="jane@example.com"
    timeZone="UTC"
    onClose={vi.fn()}
    onSent={onSent}
  />)
  await user.selectOptions(screen.getByLabelText('From matter documents'), 'agreement')
  await user.click(screen.getByLabelText(/Intake form\.pdf/))

  await user.click(screen.getByRole('button', { name: '2. Deadlines' }))
  await user.type(screen.getByLabelText(/^Due$/, { selector: '#due-fee-agreement' }), '2026-09-15')

  await user.click(screen.getByRole('button', { name: '3. Send' }))
  await user.click(screen.getByRole('button', { name: 'Send paperwork' }))

  const [, body] = api.post.mock.calls.at(-1)
  const options = JSON.parse(body.get('options'))
  expect(options.agreement_document_id).toBe('agreement')
  expect(options.agreement_due_at).toBe('2026-09-15T17:00:00.000Z')
  expect(options.selected_documents).toEqual([
    expect.objectContaining({ document_id: 'form', requires_signature: true, due_at: null }),
  ])
  expect(onSent).toHaveBeenCalledOnce()
})

it('will not send paperwork without a reviewed fee agreement', async () => {
  const user = userEvent.setup()
  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)
  await user.click(screen.getByRole('button', { name: '3. Send' }))
  expect(screen.getByRole('button', { name: 'Send paperwork' })).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('reviewed fee agreement')
  expect(api.post).not.toHaveBeenCalled()
})
