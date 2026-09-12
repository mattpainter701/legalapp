import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import * as api from '../api'
import { SignatureRequestsPanel } from './MatterDetailPage'

vi.mock('../api', async () => {
  const actual = await vi.importActual('../api')
  return Object.fromEntries(Object.entries(actual).map(([key, value]) => [key, typeof value === 'function' ? vi.fn().mockResolvedValue([]) : value]))
})
vi.mock('../App', () => ({ useAuth: () => ({ user: { id: 'user' } }) }))
vi.mock('../components/templates/GeneratedSigningPlacementReview', () => ({ default: ({ onChange }) => <button type="button" onClick={() => onChange([{ field_id: 'manual', role: 'client', source_sha256: 'verified-final' }])}>Confirm final PDF placement</button> }))

beforeEach(() => {
  vi.clearAllMocks()
  api.createSignatureRequest.mockResolvedValue({ id: 'request' })
  api.sendSignatureRequest.mockResolvedValue({ id: 'request', status: 'sent' })
  // The URL helper is synchronous; the blanket stub above makes it a promise.
  api.getMatterDocumentDownloadUrl.mockImplementation((matterId, docId) => `/api/matters/${matterId}/documents/${docId}/download`)
})
afterEach(cleanup)

async function fillSigner() {
  fireEvent.change(screen.getByPlaceholderText('Signer 1 full name'), { target: { value: 'Client Name' } })
  fireEvent.change(screen.getByPlaceholderText('Signer email'), { target: { value: 'client@example.test' } })
}

it('always sends through the portal provider with the generated PDF descriptor', async () => {
  const fields = [{ field_id: 'client', role: 'client', source_sha256: 'final' }]
  api.getMatterDocuments.mockResolvedValue({ items: [{ id: 'pdf', filename: 'Generated.pdf', positioned_fields: fields, signing_placement_required: true }] })
  render(<MemoryRouter><SignatureRequestsPanel matterId="matter" /></MemoryRouter>)
  await screen.findByRole('option', { name: 'Generated.pdf' })
  fireEvent.change(screen.getByLabelText('Document to sign'), { target: { value: 'pdf' } })
  // Dropbox Sign is gone: there is nothing to choose.
  expect(screen.queryByLabelText('Signing provider')).not.toBeInTheDocument()
  expect(screen.queryByText(/Dropbox/)).not.toBeInTheDocument()
  expect(screen.getByText(/Place signature fields on the PDF \(optional/)).toBeInTheDocument()
  await fillSigner()
  fireEvent.submit(screen.getByPlaceholderText('Signer 1 full name').closest('form'))
  await waitFor(() => expect(api.sendSignatureRequest).toHaveBeenCalledWith('matter', 'request'))
  expect(api.createSignatureRequest).toHaveBeenCalledWith('matter', expect.objectContaining({ provider: 'internal', document_id: 'pdf', positioned_fields: fields }))
  expect(await screen.findByText(/Signature request sent\. Signers will see it/)).toBeInTheDocument()
})

it('requires final placement review for a reflowed Word document before sending', async () => {
  api.getMatterDocuments.mockResolvedValue({ items: [{ id: 'word-pdf', filename: 'Reflowed.pdf', signing_placement_required: true, positioned_fields: [] }] })
  api.getMatterDocumentSigningSource.mockResolvedValue(new Blob(['final pdf']))
  render(<MemoryRouter><SignatureRequestsPanel matterId="matter" /></MemoryRouter>)
  await screen.findByRole('option', { name: 'Reflowed.pdf' })
  fireEvent.change(screen.getByLabelText('Document to sign'), { target: { value: 'word-pdf' } })
  await fillSigner()
  fireEvent.submit(screen.getByPlaceholderText('Signer 1 full name').closest('form'))
  expect(api.createSignatureRequest).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Review PDF signing positions' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Confirm final PDF placement' }))
  fireEvent.submit(screen.getByPlaceholderText('Signer 1 full name').closest('form'))
  await waitFor(() => expect(api.sendSignatureRequest).toHaveBeenCalledOnce())
  expect(api.getMatterDocumentSigningSource).toHaveBeenCalledWith('matter', 'word-pdf')
  expect(api.createSignatureRequest.mock.calls[0][1].positioned_fields[0].source_sha256).toBe('verified-final')
})


it('retains custom Word roles and rejects a partial final placement review', async () => {
  api.getMatterDocuments.mockResolvedValue({ items: [{ id: 'roles', filename: 'Roles.pdf', signing_placement_required: true, signing_roles: ['client', 'landlord'], positioned_fields: [] }] })
  api.getMatterDocumentSigningSource.mockResolvedValue(new Blob(['final pdf']))
  render(<MemoryRouter><SignatureRequestsPanel matterId="matter" /></MemoryRouter>)
  await screen.findByRole('option', { name: 'Roles.pdf' })
  fireEvent.change(screen.getByLabelText('Document to sign'), { target: { value: 'roles' } })
  expect(screen.getByRole('option', { name: 'landlord' })).toBeInTheDocument()
  await fillSigner()
  fireEvent.click(screen.getByRole('button', { name: 'Review PDF signing positions' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Confirm final PDF placement' }))
  fireEvent.submit(screen.getByPlaceholderText('Signer 1 full name').closest('form'))
  expect(await screen.findByText('Add signing fields for every role required by this document.')).toBeInTheDocument()
  expect(api.createSignatureRequest).not.toHaveBeenCalled()
})

it('sends a due date so the signature raises a follow-up task', async () => {
  api.getMatterDocuments.mockResolvedValue({ items: [{ id: 'auth', filename: 'Medical authorization.pdf' }] })
  render(<MemoryRouter><SignatureRequestsPanel matterId="matter" /></MemoryRouter>)
  await screen.findByRole('option', { name: 'Medical authorization.pdf' })
  fireEvent.change(screen.getByLabelText('Document to sign'), { target: { value: 'auth' } })
  fireEvent.change(screen.getByLabelText('Due from client'), { target: { value: '2026-10-02' } })
  await fillSigner()
  fireEvent.submit(screen.getByPlaceholderText('Signer 1 full name').closest('form'))
  await waitFor(() => expect(api.createSignatureRequest).toHaveBeenCalled())
  const [, payload] = api.createSignatureRequest.mock.calls.at(-1)
  // A deadline is what the firm chases; expiry is what voids the request.
  expect(payload.due_at).toMatch(/^2026-10-02T/)
  expect(payload.expires_at).toBeNull()
})

it('leaves the due date out when the firm sets none', async () => {
  api.getMatterDocuments.mockResolvedValue({ items: [{ id: 'auth', filename: 'Medical authorization.pdf' }] })
  render(<MemoryRouter><SignatureRequestsPanel matterId="matter" /></MemoryRouter>)
  await screen.findByRole('option', { name: 'Medical authorization.pdf' })
  fireEvent.change(screen.getByLabelText('Document to sign'), { target: { value: 'auth' } })
  await fillSigner()
  fireEvent.submit(screen.getByPlaceholderText('Signer 1 full name').closest('form'))
  await waitFor(() => expect(api.createSignatureRequest).toHaveBeenCalled())
  expect(api.createSignatureRequest.mock.calls.at(-1)[1].due_at).toBeNull()
})

it('uploads a prepared PDF to the matter and selects it as the document to sign', async () => {
  api.getMatterDocuments
    .mockResolvedValueOnce({ items: [{ id: 'old', filename: 'Existing.pdf' }] })
    .mockResolvedValue({ items: [{ id: 'old', filename: 'Existing.pdf' }, { id: 'new', filename: 'Prepared.pdf' }] })
  api.uploadMatterDocument.mockResolvedValue({ id: 'new', filename: 'Prepared.pdf', content_type: 'application/pdf' })
  render(<MemoryRouter><SignatureRequestsPanel matterId="matter" /></MemoryRouter>)
  await screen.findByRole('option', { name: 'Existing.pdf' })
  const input = screen.getByLabelText('Upload a prepared PDF')
  expect(input).toHaveAttribute('accept', 'application/pdf')
  fireEvent.change(input, { target: { files: [new File(['%PDF-1.4'], 'Prepared.pdf', { type: 'application/pdf' })] } })
  await waitFor(() => expect(api.uploadMatterDocument).toHaveBeenCalledOnce())
  const [matterId, form] = api.uploadMatterDocument.mock.calls[0]
  expect(matterId).toBe('matter')
  expect(form.get('file').name).toBe('Prepared.pdf')
  await waitFor(() => expect(screen.getByLabelText('Document to sign')).toHaveValue('new'))
  expect(screen.getByRole('option', { name: 'Prepared.pdf' })).toBeInTheDocument()
  await fillSigner()
  fireEvent.submit(screen.getByPlaceholderText('Signer 1 full name').closest('form'))
  await waitFor(() => expect(api.createSignatureRequest).toHaveBeenCalledWith('matter', expect.objectContaining({ document_id: 'new', provider: 'internal' })))
})

it('shows the upload failure inline and keeps the selection unchanged', async () => {
  api.getMatterDocuments.mockResolvedValue({ items: [{ id: 'old', filename: 'Existing.pdf' }] })
  api.uploadMatterDocument.mockRejectedValue({ response: { data: { detail: 'File exceeds maximum size of 25MB' } } })
  render(<MemoryRouter><SignatureRequestsPanel matterId="matter" /></MemoryRouter>)
  await screen.findByRole('option', { name: 'Existing.pdf' })
  fireEvent.change(screen.getByLabelText('Upload a prepared PDF'), { target: { files: [new File(['%PDF-1.4'], 'Big.pdf', { type: 'application/pdf' })] } })
  expect(await screen.findByRole('alert')).toHaveTextContent('File exceeds maximum size of 25MB')
  expect(screen.getByLabelText('Document to sign')).toHaveValue('')
})

const queued = (overrides = {}) => ({
  id: 'req-1', status: 'partially_signed', document_name: 'Fee agreement.pdf',
  sent_at: '2026-09-10T12:00:00Z', created_at: '2026-09-10T11:00:00Z', expires_at: null,
  signers: [{ id: 's1', role: 'client', name: 'Client Name', email: 'client@example.test', status: 'signed', signed_at: '2026-09-11T12:00:00Z' }],
  ...overrides,
})

it('lets staff accept an uploaded signed copy', async () => {
  api.listSignatureRequests.mockResolvedValue([queued({ submitted_document_id: 'uploaded', signature_fields_count: 2 })])
  api.acceptSignatureSubmission.mockResolvedValue(queued({ status: 'completed', executed_document_id: 'uploaded' }))
  render(<MemoryRouter><SignatureRequestsPanel matterId="matter" /></MemoryRouter>)
  expect(await screen.findByText('Signed copy uploaded — review')).toBeInTheDocument()
  expect(screen.getByText(/2 signature fields/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open uploaded copy' })).toHaveAttribute('href', expect.stringContaining('/matters/matter/documents/uploaded/download'))
  fireEvent.click(screen.getByRole('button', { name: 'Accept' }))
  await waitFor(() => expect(api.acceptSignatureSubmission).toHaveBeenCalledWith('matter', 'req-1'))
  expect(await screen.findByText('Signed copy accepted and filed to the matter.')).toBeInTheDocument()
  // The queue reloads so the row reflects the completed request.
  expect(api.listSignatureRequests).toHaveBeenCalledTimes(2)
})

it('requires a reason to reject an uploaded signed copy and sends it', async () => {
  api.listSignatureRequests.mockResolvedValue([queued({ submitted_document_id: 'uploaded' })])
  api.rejectSignatureSubmission.mockResolvedValue(queued())
  render(<MemoryRouter><SignatureRequestsPanel matterId="matter" /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('button', { name: 'Reject' }))
  expect(api.rejectSignatureSubmission).not.toHaveBeenCalled()
  expect(screen.getByText(/Tell the client what to redo/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Rejection reason'), { target: { value: 'Page 2 was not initialed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Reject' }))
  await waitFor(() => expect(api.rejectSignatureSubmission).toHaveBeenCalledWith('matter', 'req-1', { reason: 'Page 2 was not initialed' }))
  expect(await screen.findByText(/Signed copy rejected/)).toBeInTheDocument()
})

it('tells staff when the signed copy is waiting on storage, and links the filed copy once it lands', async () => {
  api.listSignatureRequests.mockResolvedValue([
    queued({ completion_pending: true, completion_error: 'Configured Microsoft OneDrive storage is unavailable' }),
    queued({ id: 'req-2', status: 'completed', executed_document_id: 'executed' }),
  ])
  render(<MemoryRouter><SignatureRequestsPanel matterId="matter" /></MemoryRouter>)
  const pending = await screen.findByRole('status')
  expect(pending).toHaveTextContent('Filing the signed copy… storage unavailable: Configured Microsoft OneDrive storage is unavailable')
  expect(pending).toHaveTextContent(/retried automatically every few minutes/)
  expect(screen.getByRole('link', { name: 'Signed copy filed' })).toHaveAttribute('href', expect.stringContaining('/documents/executed/download'))
  expect(screen.queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument()
})
