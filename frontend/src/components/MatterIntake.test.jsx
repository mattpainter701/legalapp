import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ClientIntakeChecklist from './ClientIntakeChecklist'
import { startMatterIntake } from './MatterIntakePanel'
import CaseSetupCard from './casesetup/CaseSetupCard'
import NewMatterModal from './NewMatterModal'
import api, { getClientIntake, submitClientIntake, createMatterV2, getContacts, getAdminUsers, getPlugins, getMatterPaperwork, matterPaperworkAction, getMatterDocuments, uploadClientPortalDocument } from '../api'

vi.mock('./MatterImportWizard', () => ({ default: () => <div>Historical import wizard</div> }))

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  getClientIntake: vi.fn(), submitClientIntake: vi.fn(), createMatterV2: vi.fn(), getContacts: vi.fn(), getAdminUsers: vi.fn(), getPlugins: vi.fn(), createContact: vi.fn(),
  getMatterPaperwork: vi.fn(), matterPaperworkAction: vi.fn(), getMatterDocuments: vi.fn(), getIntakeStarterPack: vi.fn(),
  uploadClientPortalDocument: vi.fn(), downloadClientPortalDocumentUrl: (id) => `/api/portal/client/documents/${id}/download`,
}))
afterEach(cleanup)
const packet = () => ({
  id: 'packet', matter_id: 'matter', status: 'awaiting_documents', requirements: { fee_agreement: { completed: false }, questionnaire: { completed: false } },
  questions: [{ key: 'summary', label: 'Describe your matter', required: true }], answers: {}, delivery: {}, sent_at: null, completed_at: null, meeting: null,
})
beforeEach(() => {
  vi.resetAllMocks()
  getClientIntake.mockResolvedValue(packet())
  api.get.mockResolvedValue({ data: packet() })
  getMatterPaperwork.mockResolvedValue(packet())
  matterPaperworkAction.mockResolvedValue(packet())
  getMatterDocuments.mockResolvedValue([])
  getContacts.mockResolvedValue([{ id: 'client', first_name: 'Jane', last_name: 'Smith', email: 'jane@example.com' }])
  getAdminUsers.mockResolvedValue([])
  getPlugins.mockResolvedValue([])
})
it('names the requested record on the client upload control', async () => {
  getClientIntake.mockResolvedValue({
    ...packet(),
    requirements: {
      fee_agreement: { completed: true },
      questionnaire: { completed: true },
      upload_1: { kind: 'upload', label: 'Marriage certificate', completed: false },
    },
  })
  render(<ClientIntakeChecklist />)
  expect(await screen.findByLabelText('Upload Marriage certificate')).toBeInTheDocument()
})
it('keeps signature outstanding after a legacy questionnaire is answered', async () => {
  const user = userEvent.setup(); const onSign = vi.fn()
  submitClientIntake.mockResolvedValue({ ...packet(), requirements: { fee_agreement: { completed: false }, questionnaire: { completed: true } } })
  render(<ClientIntakeChecklist onSign={onSign} />)
  await user.type(await screen.findByLabelText('Describe your matter *'), 'Case summary')
  await user.click(screen.getByRole('button', { name: 'Submit completed questionnaire' }))
  // Old packets carried free-text questions; once answered the questionnaire
  // has no row of its own — new packets ship it as a PDF form instead.
  await waitFor(() => expect(screen.queryByLabelText('Describe your matter *')).not.toBeInTheDocument())
  expect(submitClientIntake).toHaveBeenCalledWith({ summary: 'Case summary' })
  expect(screen.queryByText(/paperwork is complete/)).not.toBeInTheDocument()
  expect(screen.getByText('Fee agreement')).toBeInTheDocument()
  expect(screen.getByText('Needs your signature')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Open and sign' }))
  expect(onSign).toHaveBeenCalledOnce()
})
it('groups forms to sign apart from records to send, with a status on each', async () => {
  const user = userEvent.setup(); const onSign = vi.fn()
  getClientIntake.mockResolvedValue({
    ...packet(),
    questions: [],
    requirements: {
      fee_agreement: { completed: true, completed_at: '2026-09-02T10:00:00Z' },
      questionnaire: { completed: true, required: false },
      document_a: { kind: 'signature', label: 'Client intake form', completed: false, document_id: 'doc-a', signature_id: 'sig-a', due_at: '2026-09-20T17:00:00Z' },
      document_b: { kind: 'signature', label: 'Client questionnaire', completed: false, submitted_document_id: 'copy-b', document_id: 'doc-b', signature_id: 'sig-b' },
      document_c: { kind: 'signature', label: 'HIPAA release', completed: false, declined: true, document_id: 'doc-c', signature_id: 'sig-c' },
      document_d: { kind: 'document', label: 'Medical history', completed: false, document_id: 'doc-d' },
      upload_1: { kind: 'upload', label: 'Marriage certificate', completed: false },
      upload_2: { kind: 'upload', label: 'Photo ID', completed: false, submitted_document_id: 'copy-2' },
      upload_3: { kind: 'upload', label: 'Pay stubs', completed: true },
    },
  })
  render(<ClientIntakeChecklist onSign={onSign} />)
  expect(await screen.findByRole('heading', { name: 'Your paperwork' })).toBeInTheDocument()
  expect(screen.getByText('Forms to complete and sign')).toBeInTheDocument()
  expect(screen.getByText('Records to send us')).toBeInTheDocument()
  expect(screen.queryByText(/Questionnaire:/)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Submit completed questionnaire' })).not.toBeInTheDocument()

  expect(screen.getByText('Fee agreement')).toBeInTheDocument()
  expect(screen.getByText('Signed ✓')).toBeInTheDocument()
  expect(screen.getByText('Awaiting review')).toBeInTheDocument()
  expect(screen.getByText('Declined')).toBeInTheDocument()
  expect(screen.getAllByText('Needs your signature')).toHaveLength(1)
  // A form sent without a signature requirement is completed, not signed.
  expect(screen.getByText('Needs completing')).toBeInTheDocument()
  expect(screen.getByText(/Due Sep 20, 2026/)).toBeInTheDocument()
  // Only the forms still waiting on the client are actionable.
  expect(screen.getAllByRole('button', { name: 'Open and sign' })).toHaveLength(1)
  await user.click(screen.getByRole('button', { name: 'Open and sign' }))
  expect(onSign).toHaveBeenCalledOnce()
  expect(screen.getByRole('link', { name: 'Download form' })).toHaveAttribute('href', expect.stringContaining('doc-d'))
  expect(screen.getByLabelText('Upload completed form')).toHaveAttribute('accept', expect.stringContaining('application/pdf'))

  expect(screen.getByText('Needed')).toBeInTheDocument()
  expect(screen.getByText('Received — under review')).toBeInTheDocument()
  expect(screen.getByText('Accepted')).toBeInTheDocument()
  const upload = screen.getByLabelText('Upload Marriage certificate')
  expect(upload).toHaveAttribute('accept', expect.stringContaining('image/*'))
  expect(upload).toHaveAttribute('accept', expect.stringContaining('.docx'))
  expect(screen.queryByLabelText('Upload Pay stubs')).not.toBeInTheDocument()
})
it('renders a packet without a fee agreement or records', async () => {
  getClientIntake.mockResolvedValue({
    ...packet(),
    questions: [],
    requirements: {
      questionnaire: { completed: true, required: false },
      document_a: { kind: 'signature', label: 'Client intake form', completed: false, document_id: 'doc-a', signature_id: 'sig-a' },
    },
  })
  render(<ClientIntakeChecklist onSign={vi.fn()} />)
  expect(await screen.findByText('Client intake form')).toBeInTheDocument()
  expect(screen.queryByText('Fee agreement')).not.toBeInTheDocument()
  expect(screen.queryByText('Records to send us')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Open and sign' })).toBeInTheDocument()
})
it('submits an uploaded record against its requirement', async () => {
  const user = userEvent.setup()
  uploadClientPortalDocument.mockResolvedValue({ id: 'doc-up' })
  api.post.mockResolvedValue({ data: { ...packet(), requirements: { fee_agreement: { completed: true }, questionnaire: { completed: true }, upload_1: { kind: 'upload', label: 'Marriage certificate', completed: false, submitted_document_id: 'doc-up' } } } })
  getClientIntake.mockResolvedValue({ ...packet(), questions: [], requirements: { fee_agreement: { completed: true }, questionnaire: { completed: true }, upload_1: { kind: 'upload', label: 'Marriage certificate', completed: false } } })
  render(<ClientIntakeChecklist />)
  const file = new File(['scan'], 'certificate.pdf', { type: 'application/pdf' })
  await user.upload(await screen.findByLabelText('Upload Marriage certificate'), file)
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/portal/client/intake/requirements/upload_1/submission', { document_id: 'doc-up' }))
  expect(uploadClientPortalDocument).toHaveBeenCalledWith(file, 'Intake: upload_1')
  expect(await screen.findByText('Received — under review')).toBeInTheDocument()
})
it('preserves answers when submission fails', async () => {
  const user = userEvent.setup()
  submitClientIntake.mockRejectedValue({ response: { data: { detail: 'Storage unavailable; please retry' } } })
  render(<ClientIntakeChecklist />)
  await user.type(await screen.findByLabelText('Describe your matter *'), 'My answer')
  await user.click(screen.getByRole('button', { name: 'Submit completed questionnaire' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Storage unavailable')
  expect(screen.getByLabelText('Describe your matter *')).toHaveValue('My answer')
})
it('requires explicit reviewed retry for uncertain delivery', async () => {
  const user = userEvent.setup()
  getMatterPaperwork.mockResolvedValue({ ...packet(), delivery: { 'welcome:email': { state: 'unknown', attempt: 0 } } })
  render(<CaseSetupCard matterId="matter" />)
  await user.click(await screen.findByRole('button', { name: 'Review delivery' }))
  expect(matterPaperworkAction).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: /I verified it was not sent/ }))
  expect(matterPaperworkAction).toHaveBeenCalledWith('matter', 'retry', { delivery_key: 'welcome:email', confirm_not_sent: true })
})
it('records the selected external document and verification note', async () => {
  const user = userEvent.setup()
  getMatterDocuments.mockResolvedValue([{ id: 'doc', filename: 'Executed agreement.pdf' }])
  render(<CaseSetupCard matterId="matter" />)
  await user.click(await screen.findByText(/Review received documents/))
  await user.selectOptions(screen.getByLabelText('Received document'), 'doc')
  await user.type(screen.getByLabelText('Verification note'), 'Reviewed signature')
  await user.click(screen.getByRole('button', { name: 'Confirm document is complete' }))
  expect(matterPaperworkAction).toHaveBeenCalledWith('matter', 'receipt', { requirement: 'fee_agreement', document_id: 'doc', note: 'Reviewed signature' })
})
it('offers call or in-person booking after both requirements complete', async () => {
  const user = userEvent.setup()
  getMatterPaperwork.mockResolvedValue({ ...packet(), status: 'documents_complete', completed_at: '2026-09-06T14:00:00Z', requirements: { fee_agreement: { completed: true }, questionnaire: { completed: true } } })
  render(<CaseSetupCard matterId="matter" />)
  await user.selectOptions(await screen.findByLabelText('Meeting type'), 'in_person')
  fireEvent.change(screen.getByLabelText('Meeting date and time'), { target: { value: '2026-09-08T10:00' } })
  await user.type(screen.getByLabelText('Call details or office location'), 'Main office')
  await user.click(screen.getByRole('button', { name: 'Save meeting & notify client' }))
  expect(matterPaperworkAction).toHaveBeenCalledWith('matter', 'meeting', expect.objectContaining({ kind: 'in_person', details: 'Main office', starts_at: expect.stringMatching(/Z$/) }))
})
it('creates the matter from the modal without starting client intake', async () => {
  const user = userEvent.setup(); const onCreated = vi.fn()
  createMatterV2.mockResolvedValue({ id: 'matter', matter_name: 'Smith case' })
  render(<NewMatterModal open onClose={vi.fn()} onCreated={onCreated} />)
  await user.type(screen.getByLabelText(/Matter Title/), 'Smith case')
  await waitFor(() => expect(getContacts).toHaveBeenCalled())
  await user.selectOptions(screen.getByLabelText(/^Client$/), 'client')
  await user.click(screen.getByRole('button', { name: 'Open Matter' }))
  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce())
  expect(createMatterV2).toHaveBeenCalledOnce()
  // Creating a matter no longer mounts an intake packet, so nothing posts to it.
  expect(api.post).not.toHaveBeenCalled()
})

it('clears the previous intake when switching to a matter without a packet', async () => {
  const { rerender } = render(<CaseSetupCard matterId="first" />)
  await screen.findByRole('heading', { name: 'Client paperwork' })
  getMatterPaperwork.mockRejectedValue({ response: { status: 404 } })
  rerender(<CaseSetupCard matterId="second" />)
  await screen.findByRole('button', { name: 'Send client paperwork' })
  expect(screen.queryByRole('heading', { name: 'Client paperwork' })).not.toBeInTheDocument()
})

it('keeps the matter form out of the historical import path', async () => {
  const user = userEvent.setup()
  render(<NewMatterModal open onClose={vi.fn()} onCreated={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'Open Matter' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Import existing matters' }))
  expect(screen.getByText('Historical import wizard')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Open Matter' })).not.toBeInTheDocument()
  expect(api.post).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'New matter' }))
  expect(screen.getByRole('button', { name: 'Open Matter' })).toBeInTheDocument()
})

it('sends the paperwork form as multipart so the server receives its fields', async () => {
  api.post.mockResolvedValue({ data: packet() })
  await startMatterIntake('matter', { email: 'jane@example.com' }, null)
  const [path, body, config] = api.post.mock.calls.at(-1)
  expect(path).toBe('/matters/matter/intake')
  expect(body).toBeInstanceOf(FormData)
  expect(body.get('options')).toContain('jane@example.com')
  // The shared client defaults to JSON, which drops every form field.
  expect(config.headers['Content-Type']).toBe('multipart/form-data')
})
