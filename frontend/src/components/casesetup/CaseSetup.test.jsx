import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CaseSetupCard from './CaseSetupCard'
import PaperworkDrawer from './PaperworkDrawer'
import { dueDateToIso, paperworkOptions } from './paperwork'
import api, {
  getAdminUsers, getIntakeStarterPack, getMatterDocuments, getMatterPaperwork, matterPaperworkAction, uploadMatterDocument,
} from '../../api'

vi.mock('../../api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  getMatterPaperwork: vi.fn(), matterPaperworkAction: vi.fn(), getMatterDocuments: vi.fn(),
  getAdminUsers: vi.fn(), getContacts: vi.fn(), getIntakeStarterPack: vi.fn(),
  uploadMatterDocument: vi.fn(),
}))

// The real picker lazy-loads the Template Studio render modal. Stand in for it
// with the same contract: pinned to the matter, reports the render response
// through onSaved, and is dismissed through onClose.
const renderResponse = {
  rendered: '', matter_document_id: 'rendered-doc', output_format: 'pdf',
  output_filename: 'Engagement Letter.pdf',
  download_url: '/api/matters/matter/documents/rendered-doc/download',
}
vi.mock('../templates/MatterTemplatePicker', () => ({
  default: ({ matterId, onSaved, onClose }) => (
    <div role="dialog" aria-label="Attach template">
      <p>Preparing for {matterId}</p>
      <button type="button" onClick={() => onSaved(renderResponse)}>Save to matter</button>
      <button type="button" onClick={onClose}>Close</button>
    </div>
  ),
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

it('prepares the fee agreement from a firm template and sends it', async () => {
  const user = userEvent.setup()
  // The matter's own record of the saved document carries the content type
  // the render response omits.
  getMatterDocuments.mockResolvedValue({
    items: [{ id: 'rendered-doc', filename: 'Engagement Letter.pdf', content_type: 'application/pdf' }],
    total: 1,
  })

  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)

  const prepareAgreement = screen.getByRole('button', { name: /Prepare the fee agreement/ })
  await user.click(prepareAgreement)
  const picker = screen.getByRole('dialog', { name: 'Attach template' })
  expect(picker).toHaveTextContent('Preparing for matter')
  await user.click(within(picker).getByRole('button', { name: 'Save to matter' }))
  await user.click(within(picker).getByRole('button', { name: 'Close' }))

  await waitFor(() => expect(getMatterDocuments).toHaveBeenCalledWith('matter', { limit: 200 }))
  expect(screen.queryByRole('dialog', { name: 'Attach template' })).not.toBeInTheDocument()
  // The saved document is listed and pre-selected as the fee agreement, not
  // offered again as an additional form.
  expect(await screen.findByLabelText('From matter documents')).toHaveValue('rendered-doc')
  expect(screen.queryByRole('checkbox', { name: /Engagement Letter\.pdf/ })).not.toBeInTheDocument()

  // The prepared agreement makes the packet sendable.
  await user.click(screen.getByRole('button', { name: '3. Send' }))
  await user.click(screen.getByRole('button', { name: 'Send paperwork' }))
  const [, body] = api.post.mock.calls.at(-1)
  const options = JSON.parse(body.get('options'))
  expect(options.agreement_document_id).toBe('rendered-doc')
  expect(options.selected_documents).toEqual([])
})

it('prepares an additional signing form from a firm template', async () => {
  const user = userEvent.setup()
  // No matter listing yet: the drawer falls back to the render response.
  getMatterDocuments.mockResolvedValue([])

  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)

  const prepareForm = screen.getByRole('button', { name: /Prepare an additional form/ })
  await user.click(prepareForm)
  await user.click(screen.getByRole('button', { name: 'Save to matter' }))

  const form = await screen.findByRole('checkbox', { name: /Engagement Letter\.pdf/ })
  expect(form).toBeChecked()
  expect(screen.getByRole('checkbox', { name: /Track signature/ })).toBeChecked()
  // It is a signing form; the fee agreement is still unchosen.
  expect(screen.getByLabelText('From matter documents')).toHaveValue('')
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

it('sends without a fee agreement when another standard piece is included', async () => {
  const user = userEvent.setup()
  const onSent = vi.fn()
  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={onSent} />)
  await user.click(screen.getByRole('button', { name: '3. Send' }))
  const send = screen.getByRole('button', { name: 'Send paperwork' })
  expect(send).toBeEnabled()
  await user.click(send)
  const [, body] = api.post.mock.calls.at(-1)
  const options = JSON.parse(body.get('options'))
  expect(options.agreement_document_id).toBeNull()
  expect(onSent).toHaveBeenCalledOnce()
})

it('will not send a packet with nothing included', async () => {
  const user = userEvent.setup()
  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)
  await user.click(screen.getByLabelText(/Client questionnaire/))
  await user.click(screen.getByRole('button', { name: '3. Send' }))
  expect(screen.getByRole('button', { name: 'Send paperwork' })).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('at least one')
  expect(api.post).not.toHaveBeenCalled()
})

it('includes the client intake form as an unsigned document', async () => {
  const user = userEvent.setup()
  render(<PaperworkDrawer
    matterId="matter"
    documents={[{ id: 'intake', filename: 'Client intake form.pdf', content_type: 'application/pdf' }]}
    clientEmail="jane@example.com"
    timeZone="UTC"
    onClose={vi.fn()}
    onSent={vi.fn()}
  />)
  await user.selectOptions(screen.getByLabelText('Choose the client intake form'), 'intake')
  await user.click(screen.getByRole('button', { name: '3. Send' }))
  await user.click(screen.getByRole('button', { name: 'Send paperwork' }))
  const [, body] = api.post.mock.calls.at(-1)
  const options = JSON.parse(body.get('options'))
  expect(options.selected_documents).toEqual([
    expect.objectContaining({ document_id: 'intake', requires_signature: false }),
  ])
})

it('seeds the matter type questions and upload hint from the starter pack', async () => {
  getIntakeStarterPack.mockResolvedValue({
    practice: 'family',
    practice_label: 'Family and domestic relations',
    questions: [{ key: 'matter_summary', label: 'What happened?' }],
    upload_requirements: [{ key: 'upload_family_orders', label: 'Any existing court orders' }],
  })
  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)
  await waitFor(() => expect(getIntakeStarterPack).toHaveBeenCalledWith({ matter_id: 'matter' }))
  expect(await screen.findByPlaceholderText('Any existing court orders')).toBeInTheDocument()
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
