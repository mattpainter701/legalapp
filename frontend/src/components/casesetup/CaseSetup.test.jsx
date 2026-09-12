import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CaseSetupCard from './CaseSetupCard'
import PaperworkDrawer from './PaperworkDrawer'
import { dueDateToIso, orderedRequirements, paperworkOptions, requirementState } from './paperwork'
import api, {
  getAdminUsers, getIntakeStarterPack, getMatterDocuments, getMatterPaperwork, matterPaperworkAction, previewMatterPaperwork, uploadMatterDocument,
} from '../../api'

vi.mock('../../api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  getMatterPaperwork: vi.fn(), matterPaperworkAction: vi.fn(), previewMatterPaperwork: vi.fn(),
  getMatterDocuments: vi.fn(),
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
  previewMatterPaperwork.mockResolvedValue({
    subject: 'Painter Law: Please review and complete your paperwork',
    html_body: '<!DOCTYPE html><div class="header"><h1>Painter Law</h1></div><p>Open Secure Client Portal</p>',
    text_body: 'Hi Jane, open your secure client portal.',
    sms_body: 'Painter Law: Your paperwork is ready in your secure portal.',
  })
  api.post.mockResolvedValue({ data: packet() })
})

it('states a due date at 5pm in the client timezone, not in UTC', () => {
  // A naive date would chase a Chicago client at 7pm local on the day before.
  expect(dueDateToIso('2026-09-15', 'America/Chicago')).toBe('2026-09-15T22:00:00.000Z')
  expect(dueDateToIso('2026-09-15', 'UTC')).toBe('2026-09-15T17:00:00.000Z')
  expect(dueDateToIso('', 'UTC')).toBeNull()
})

it('builds the full intake request from a complete draft', () => {
  const options = paperworkOptions({
    email: 'jane@example.com', channels: ['email'], smsPermissionVerified: false, ownerId: '',
    agreementDocumentId: 'agreement', agreementDue: '2026-09-15',
    intakeFormDocumentId: 'intake', intakeFormLabel: 'Client intake form.pdf', intakeFormRequiresSignature: true, intakeFormDue: '2026-09-16',
    questionnaireDocumentId: 'questionnaire', questionnaireLabel: 'Family questionnaire.pdf', questionnaireRequiresSignature: false, questionnaireDue: '2026-09-18',
    forms: [{ documentId: 'form', label: 'Retainer addendum', requiresSignature: true, due: '2026-09-20' }],
    requestUploads: true, uploads: 'Marriage certificate\n\nRecent pay stubs', uploadsDue: '2026-09-25', portalAfterSigning: true,
  }, 'UTC')
  expect(options).toEqual({
    email: 'jane@example.com',
    channels: ['email'],
    timezone: 'UTC',
    owner_id: null,
    sms_permission_verified: false,
    sms_case_updates_verified: false,
    agreement_document_id: 'agreement',
    agreement_due_at: '2026-09-15T17:00:00.000Z',
    // The server's free-text questionnaire is never requested any more: the
    // questionnaire is a PDF the firm supplies, sent like any other form.
    questionnaire_due_at: null,
    include_questionnaire: false,
    questions: [],
    portal_after_signing: true,
    selected_documents: [
      { document_id: 'intake', label: 'Client intake form.pdf', requires_signature: true, due_at: '2026-09-16T17:00:00.000Z' },
      { document_id: 'questionnaire', label: 'Family questionnaire.pdf', requires_signature: false, due_at: '2026-09-18T17:00:00.000Z' },
      { document_id: 'form', label: 'Retainer addendum', requires_signature: true, due_at: '2026-09-20T17:00:00.000Z' },
    ],
    upload_requirements: [
      { key: 'upload_1', label: 'Marriage certificate', required: true, due_at: '2026-09-25T17:00:00.000Z' },
      { key: 'upload_2', label: 'Recent pay stubs', required: true, due_at: '2026-09-25T17:00:00.000Z' },
    ],
    confirm_send: true,
  })
})

it('sends no records unless the section is switched on', () => {
  // A suggested list left in the textarea after the section was switched off
  // must not quietly become a requirement.
  const options = paperworkOptions({
    email: 'jane@example.com', channels: ['email'], forms: [],
    requestUploads: false, uploads: 'Marriage certificate', uploadsDue: '2026-09-25',
  }, 'UTC')
  expect(options.upload_requirements).toEqual([])
  expect(options.selected_documents).toEqual([])
  expect(options.include_questionnaire).toBe(false)
})

it('labels each requirement by how the client completes it', () => {
  expect(requirementState({ completed: false }, 'fee_agreement')).toMatchObject({ label: 'Needs signature' })
  expect(requirementState({ completed: true }, 'fee_agreement')).toMatchObject({ label: 'Signed' })
  expect(requirementState({ completed: false, kind: 'signature' }, 'document_1')).toMatchObject({ label: 'Needs signature' })
  expect(requirementState({ completed: false, kind: 'signature', submitted_document_id: 'copy' }, 'document_1')).toMatchObject({ label: 'Awaiting review' })
  expect(requirementState({ completed: true, kind: 'signature' }, 'document_1')).toMatchObject({ label: 'Signed' })
  expect(requirementState({ completed: false, kind: 'upload' }, 'upload_1')).toMatchObject({ label: 'Outstanding' })
  expect(requirementState({ completed: true, kind: 'upload' }, 'upload_1')).toMatchObject({ label: 'Received' })
  expect(requirementState({ completed: true, kind: 'document' }, 'document_2')).toMatchObject({ label: 'Received' })
})

it('hides the pre-completed legacy questionnaire row from the strip', () => {
  const rows = orderedRequirements(packet({
    requirements: {
      fee_agreement: { completed: false },
      questionnaire: { completed: true, required: false },
      upload_1: { completed: false, kind: 'upload', label: 'Pay stubs' },
    },
  }))
  expect(rows.map(row => row.key)).toEqual(['fee_agreement', 'upload_1'])
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
  expect(screen.getByText('Needs signature')).toBeInTheDocument()
})

it('labels the strip by how each requirement completes', async () => {
  getMatterPaperwork.mockResolvedValue(packet({
    requirements: {
      fee_agreement: { completed: true, completed_at: '2026-09-02T12:00:00Z' },
      document_a: { completed: false, kind: 'signature', label: 'Client intake form', submitted_document_id: 'copy' },
      upload_1: { completed: true, kind: 'upload', label: 'Pay stubs' },
      upload_2: { completed: false, kind: 'upload', label: 'Existing orders' },
    },
  }))
  render(<CaseSetupCard matterId="matter" />)
  expect(await screen.findByText('Signed')).toBeInTheDocument()
  expect(screen.getByText('Awaiting review')).toBeInTheDocument()
  expect(screen.getByText('Received')).toBeInTheDocument()
  expect(screen.getByText('Outstanding')).toBeInTheDocument()
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
  // The Documents step reads top to bottom in the order the client meets them.
  expect(screen.getAllByRole('heading', { level: 3 }).map(heading => heading.textContent)).toEqual([
    'Fee agreement (optional)', 'Client intake form (optional)', 'Client questionnaire (optional)', 'Additional forms',
  ])
  // Records are an explicit opt-in, collapsed until switched on.
  expect(screen.getByRole('checkbox', { name: /Records to request from the client/ })).not.toBeChecked()
  expect(screen.queryByLabelText('One record per line')).not.toBeInTheDocument()
  // Nothing about the retired free-text questionnaire remains.
  expect(screen.queryByText(/Reset to standard questions/)).not.toBeInTheDocument()
  expect(screen.queryByLabelText('One question per line')).not.toBeInTheDocument()

  await user.selectOptions(screen.getByLabelText('Choose the fee agreement'), 'agreement')
  await user.click(screen.getByLabelText(/Intake form\.pdf/))
  expect(screen.getByRole('checkbox', { name: 'Client signs this form' })).toBeChecked()

  await user.click(screen.getByRole('button', { name: '2. Deadlines' }))
  await user.type(screen.getByLabelText(/^Due$/, { selector: '#due-fee-agreement' }), '2026-09-15')
  expect(screen.queryByLabelText(/^Due$/, { selector: '#due-uploads' })).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '3. Send' }))
  await user.click(screen.getByRole('button', { name: 'Send paperwork' }))

  const [, body] = api.post.mock.calls.at(-1)
  const options = JSON.parse(body.get('options'))
  expect(options.agreement_document_id).toBe('agreement')
  expect(options.agreement_due_at).toBe('2026-09-15T17:00:00.000Z')
  expect(options.selected_documents).toEqual([
    expect.objectContaining({ document_id: 'form', requires_signature: true, due_at: null }),
  ])
  expect(options.upload_requirements).toEqual([])
  expect(options.include_questionnaire).toBe(false)
  expect(options.questions).toEqual([])
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
  expect(await screen.findByLabelText('Choose the fee agreement')).toHaveValue('rendered-doc')
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
  expect(screen.getByRole('checkbox', { name: 'Client signs this form' })).toBeChecked()
  // It is a signing form; the fee agreement is still unchosen.
  expect(screen.getByLabelText('Choose the fee agreement')).toHaveValue('')
})

it('attaches a locally filled form uploaded through the additional forms card', async () => {
  const user = userEvent.setup()
  uploadMatterDocument.mockResolvedValue({
    id: 'filled-doc', filename: 'Filled form.pdf', content_type: 'application/pdf',
  })

  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)

  await user.upload(screen.getByLabelText('Upload an additional form'), new File(['%PDF-1.4'], 'Filled form.pdf', { type: 'application/pdf' }))

  await waitFor(() => expect(uploadMatterDocument).toHaveBeenCalledOnce())
  expect(await screen.findByRole('checkbox', { name: /Filled form\.pdf/ })).toBeChecked()
})

it('uploads the fee agreement to the matter so one control names the choice', async () => {
  const user = userEvent.setup()
  uploadMatterDocument.mockResolvedValue({
    id: 'uploaded-agreement', filename: 'Signed fee agreement.pdf', content_type: 'application/pdf',
  })

  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)

  await user.upload(
    screen.getByLabelText('Upload a prepared fee agreement'),
    new File(['%PDF-1.4'], 'Signed fee agreement.pdf', { type: 'application/pdf' }),
  )

  await waitFor(() => expect(uploadMatterDocument).toHaveBeenCalledOnce())
  // The upload lands in the same select the dropdown and the template picker
  // write to, so no second control is left claiming nothing is chosen.
  expect(await screen.findByLabelText('Choose the fee agreement')).toHaveValue('uploaded-agreement')
  // It is the fee agreement, not an additional form.
  expect(screen.queryByRole('checkbox', { name: /Signed fee agreement\.pdf/ })).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '3. Send' }))
  await user.click(screen.getByRole('button', { name: 'Send paperwork' }))
  const [, body] = api.post.mock.calls.at(-1)
  expect(body.get('agreement')).toBeNull()
  const options = JSON.parse(body.get('options'))
  expect(options.agreement_document_id).toBe('uploaded-agreement')
  expect(options.selected_documents).toEqual([])
})

it('sends without a fee agreement when another standard piece is included', async () => {
  const user = userEvent.setup()
  const onSent = vi.fn()
  render(<PaperworkDrawer
    matterId="matter"
    documents={[{ id: 'questionnaire', filename: 'Family questionnaire.pdf', content_type: 'application/pdf' }]}
    clientEmail="jane@example.com"
    timeZone="UTC"
    onClose={vi.fn()}
    onSent={onSent}
  />)
  await user.selectOptions(screen.getByLabelText('Choose the client questionnaire'), 'questionnaire')
  await user.click(screen.getByRole('button', { name: '3. Send' }))
  const send = screen.getByRole('button', { name: 'Send paperwork' })
  expect(send).toBeEnabled()
  await user.click(send)
  const [, body] = api.post.mock.calls.at(-1)
  const options = JSON.parse(body.get('options'))
  expect(options.agreement_document_id).toBeNull()
  expect(options.selected_documents).toEqual([
    { document_id: 'questionnaire', label: 'Family questionnaire.pdf', requires_signature: true, due_at: null },
  ])
  expect(onSent).toHaveBeenCalledOnce()
})

it('will not send a packet with nothing included', async () => {
  const user = userEvent.setup()
  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)
  // Switching records on without listing any is still nothing.
  await user.click(screen.getByRole('checkbox', { name: /Records to request from the client/ }))
  await user.click(screen.getByRole('button', { name: '3. Send' }))
  expect(screen.getByRole('button', { name: 'Send paperwork' })).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('at least one')
  expect(api.post).not.toHaveBeenCalled()
})

it('sends the client intake form signed by default, or unsigned when the toggle is off', async () => {
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
  // The chosen form is no longer offered as an additional form.
  expect(screen.queryByRole('checkbox', { name: /Client intake form\.pdf/ })).not.toBeInTheDocument()
  const signs = screen.getByRole('checkbox', { name: 'Client signs this form' })
  expect(signs).toBeChecked()
  await user.click(signs)

  await user.click(screen.getByRole('button', { name: '2. Deadlines' }))
  await user.type(screen.getByLabelText(/^Due$/, { selector: '#due-intake-form' }), '2026-09-16')

  await user.click(screen.getByRole('button', { name: '3. Send' }))
  await user.click(screen.getByRole('button', { name: 'Send paperwork' }))
  const [, body] = api.post.mock.calls.at(-1)
  const options = JSON.parse(body.get('options'))
  expect(options.selected_documents).toEqual([
    { document_id: 'intake', label: 'Client intake form.pdf', requires_signature: false, due_at: '2026-09-16T17:00:00.000Z' },
  ])
})

it('prepares the questionnaire from a firm template and sends it as a signed form', async () => {
  const user = userEvent.setup()
  getMatterDocuments.mockResolvedValue([])
  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)

  await user.click(screen.getByRole('button', { name: /Prepare the client questionnaire/ }))
  await user.click(screen.getByRole('button', { name: 'Save to matter' }))
  expect(await screen.findByLabelText('Choose the client questionnaire')).toHaveValue('rendered-doc')
  expect(screen.getByRole('checkbox', { name: 'Client signs this form' })).toBeChecked()

  await user.click(screen.getByRole('button', { name: '2. Deadlines' }))
  await user.type(screen.getByLabelText(/^Due$/, { selector: '#due-questionnaire' }), '2026-09-18')

  await user.click(screen.getByRole('button', { name: '3. Send' }))
  await user.click(screen.getByRole('button', { name: 'Send paperwork' }))
  const [, body] = api.post.mock.calls.at(-1)
  const options = JSON.parse(body.get('options'))
  expect(options.include_questionnaire).toBe(false)
  expect(options.selected_documents).toEqual([
    { document_id: 'rendered-doc', label: 'Engagement Letter.pdf', requires_signature: true, due_at: '2026-09-18T17:00:00.000Z' },
  ])
})

it('previews the exact branded message the client will receive', async () => {
  const user = userEvent.setup()
  render(<PaperworkDrawer
    matterId="matter"
    documents={[{ id: 'intake', filename: 'Client intake form.pdf', content_type: 'application/pdf' }]}
    clientEmail="jane@example.com"
    timeZone="UTC"
    onClose={vi.fn()}
    onSent={vi.fn()}
  />)

  // The preview waits for a sendable draft.
  await user.selectOptions(screen.getByLabelText('Choose the client intake form'), 'intake')
  await user.click(screen.getByRole('button', { name: '3. Send' }))

  // The server renders the copy; the drawer only displays it.
  expect(await screen.findByText('Painter Law: Please review and complete your paperwork')).toBeInTheDocument()
  await waitFor(() => expect(previewMatterPaperwork).toHaveBeenCalledWith(
    'matter', expect.objectContaining({ email: 'jane@example.com' }),
  ))
  // The branded HTML is sandboxed so a template can never run script.
  expect(screen.getByTitle('Client email preview')).toHaveAttribute('sandbox')

  await user.click(screen.getByRole('tab', { name: 'Text' }))
  expect(await screen.findByText('Painter Law: Your paperwork is ready in your secure portal.')).toBeInTheDocument()
})

it('offers the starter pack records only when the firm asks for them', async () => {
  const user = userEvent.setup()
  getIntakeStarterPack.mockResolvedValue({
    practice: 'family',
    practice_label: 'Family and domestic relations',
    questions: [{ key: 'matter_summary', label: 'What happened?' }],
    upload_requirements: [
      { key: 'upload_family_orders', label: 'Any existing court orders' },
      { key: 'upload_family_income', label: 'Recent pay stubs' },
    ],
  })
  render(<PaperworkDrawer matterId="matter" documents={[]} clientEmail="jane@example.com" timeZone="UTC" onClose={vi.fn()} onSent={vi.fn()} />)
  await waitFor(() => expect(getIntakeStarterPack).toHaveBeenCalledWith({ matter_id: 'matter' }))

  await user.click(screen.getByRole('checkbox', { name: /Records to request from the client/ }))
  const records = screen.getByLabelText('One record per line')
  // Switched on, the list is still empty: the pack is a suggestion, not a default.
  expect(records).toHaveValue('')
  expect(records).toHaveAttribute('placeholder', 'Any existing court orders\nRecent pay stubs')
  expect(screen.getByText(/Records to send us/)).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: 'Use the suggested list for Family and domestic relations' }))
  expect(records).toHaveValue('Any existing court orders\nRecent pay stubs')

  await user.click(screen.getByRole('button', { name: '2. Deadlines' }))
  await user.type(screen.getByLabelText(/^Due$/, { selector: '#due-uploads' }), '2026-09-25')

  await user.click(screen.getByRole('button', { name: '3. Send' }))
  expect(screen.getByText('2 requested records — due 2026-09-25')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Send paperwork' }))
  const [, body] = api.post.mock.calls.at(-1)
  const options = JSON.parse(body.get('options'))
  expect(options.upload_requirements).toEqual([
    { key: 'upload_1', label: 'Any existing court orders', required: true, due_at: '2026-09-25T17:00:00.000Z' },
    { key: 'upload_2', label: 'Recent pay stubs', required: true, due_at: '2026-09-25T17:00:00.000Z' },
  ])
})

it('keeps case-update texts out of an intake-only consent', () => {
  // A client who agreed to onboarding texts has not agreed to be texted for
  // the life of the matter; the two permissions travel separately.
  const base = {
    email: 'jane@example.com', channels: ['email', 'sms'], forms: [], uploads: '',
    smsPermissionVerified: true,
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
