import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CaseSetupCard from './CaseSetupCard'
import PaperworkDrawer from './PaperworkDrawer'
import { dueDateToIso, paperworkOptions } from './paperwork'
import api, {
  getAdminUsers, getMatterDocuments, getMatterPaperwork, matterPaperworkAction,
  getSampleTemplates, getSampleTemplateSource, getTemplateSource, getTemplates,
  triggerBlobDownload, uploadMatterDocument,
} from '../../api'

vi.mock('../../api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  getMatterPaperwork: vi.fn(), matterPaperworkAction: vi.fn(), getMatterDocuments: vi.fn(),
  getAdminUsers: vi.fn(), getContacts: vi.fn(), getIntakeStarterPack: vi.fn(),
  getTemplates: vi.fn(), getSampleTemplates: vi.fn(),
  getTemplateSource: vi.fn(), getSampleTemplateSource: vi.fn(), triggerBlobDownload: vi.fn(),
  uploadMatterDocument: vi.fn(),
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
  getTemplates.mockResolvedValue({ items: [] })
  getSampleTemplates.mockResolvedValue({ items: [] })
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

it('downloads a blank sample form from the paperwork library', async () => {
  const user = userEvent.setup()
  getSampleTemplates.mockResolvedValue({
    items: [{ id: 'sample-lease', title: 'ND Residential Lease', category: 'leases', format: 'pdf' }],
  })
  const blob = new Blob(['%PDF-1.4'], { type: 'application/pdf' })
  getSampleTemplateSource.mockResolvedValue(blob)

  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)

  await user.click(screen.getByRole('button', { name: /Download a firm template or sample/ }))
  await user.click(await screen.findByRole('tab', { name: 'Sample forms' }))
  await user.click(await screen.findByRole('button', { name: 'Download' }))

  await waitFor(() => expect(getSampleTemplateSource).toHaveBeenCalledWith('sample-lease'))
  expect(triggerBlobDownload).toHaveBeenCalledWith(blob, 'ND Residential Lease.pdf')
})

it('downloads a blank firm template from the paperwork library', async () => {
  const user = userEvent.setup()
  const id = '22222222-2222-4222-8222-222222222222'
  getTemplates.mockResolvedValue({
    items: [{ id, title: 'Engagement Letter', format: 'pdf', source_filename: 'letter.pdf' }],
  })
  const file = new File(['%PDF-1.4'], 'letter.pdf', { type: 'application/pdf' })
  getTemplateSource.mockResolvedValue(file)

  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)

  await user.click(screen.getByRole('button', { name: /Download a firm template or sample/ }))
  await user.click(await screen.findByRole('button', { name: 'Download' }))

  await waitFor(() => expect(getTemplateSource).toHaveBeenCalledWith(id, 'letter.pdf'))
  expect(triggerBlobDownload).toHaveBeenCalledWith(file, 'letter.pdf')
})

it('opens a firm template in Template Studio from the paperwork library', async () => {
  const user = userEvent.setup()
  const id = '11111111-1111-4111-8111-111111111111'
  getTemplates.mockResolvedValue({
    items: [{ id, title: 'Engagement Letter', format: 'pdf', source_filename: 'letter.pdf' }],
  })

  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)

  await user.click(screen.getByRole('button', { name: /Download a firm template or sample/ }))
  const link = await screen.findByRole('link', { name: /Open in Studio/ })
  expect(link).toHaveAttribute('href', `/templates/${id}/studio`)
})

it('attaches a locally filled form uploaded through Choose a file', async () => {
  const user = userEvent.setup()
  uploadMatterDocument.mockResolvedValue({
    id: 'filled-doc', filename: 'Filled form.pdf', content_type: 'application/pdf',
  })

  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)

  await user.upload(screen.getByLabelText('Choose a file'), new File(['%PDF-1.4'], 'Filled form.pdf', { type: 'application/pdf' }))

  await waitFor(() => expect(uploadMatterDocument).toHaveBeenCalledOnce())
  expect(await screen.findByRole('checkbox', { name: /Filled form\.pdf/ })).toBeChecked()
})

it('will not send paperwork without a reviewed fee agreement', async () => {
  const user = userEvent.setup()
  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)
  await user.click(screen.getByRole('button', { name: '3. Send' }))
  expect(screen.getByRole('button', { name: 'Send paperwork' })).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('reviewed fee agreement')
  expect(api.post).not.toHaveBeenCalled()
})

it('keeps case-update texts out of an intake-only consent', () => {
  // A client who agreed to onboarding texts has not agreed to be texted for
  // the life of the matter; the two permissions travel separately.
  const base = {
    email: 'jane@example.com', channels: ['email', 'sms'], forms: [], uploads: '',
    includeQuestionnaire: false, questions: '', smsPermissionVerified: true,
  }
  expect(paperworkOptions(base, 'UTC').sms_case_updates_verified).toBe(false)
  expect(paperworkOptions({ ...base, smsCaseUpdatesVerified: true }, 'UTC').sms_case_updates_verified).toBe(true)
})

it('reports every polled packet so the page can alert on a new signature', async () => {
  const onPacketChange = vi.fn()
  render(<CaseSetupCard matterId="matter" onPacketChange={onPacketChange} />)
  await screen.findByRole('heading', { name: 'Client paperwork' })
  expect(onPacketChange).toHaveBeenCalledWith(expect.objectContaining({ status: 'awaiting_documents' }))
})

it('reports the absence of a packet as null', async () => {
  const onPacketChange = vi.fn()
  getMatterPaperwork.mockRejectedValue({ response: { status: 404 } })
  render(<CaseSetupCard matterId="matter" onPacketChange={onPacketChange} />)
  await screen.findByRole('button', { name: 'Send client paperwork' })
  expect(onPacketChange).toHaveBeenCalledWith(null)
})
