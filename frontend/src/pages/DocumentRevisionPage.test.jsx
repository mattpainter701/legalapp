import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DocumentRevisionPage from './DocumentRevisionPage'

const apiMocks = vi.hoisted(() => ({
  approveMatterDocumentRevision: vi.fn(),
  createMatterDocumentRevision: vi.fn(),
  getMatterDocumentRevision: vi.fn(),
  getMatterDocumentRevisionArtifactUrl: vi.fn(),
  getMatterDocuments: vi.fn(),
  listMatterDocumentRevisions: vi.fn(),
  listSignatureRequests: vi.fn(),
  prepareMatterDocumentRevisionESignReplacement: vi.fn(),
  rejectMatterDocumentRevision: vi.fn(),
}))

vi.mock('../api', () => apiMocks)

const SHA = 'a'.repeat(64)

const readyRevision = {
  id: 'revision-1',
  matter_id: 'matter-1',
  root_document_id: 'root-doc',
  source_document_id: 'source-doc',
  output_document_id: 'output-doc',
  source_filename: 'Engagement agreement.docx',
  source_sha256: 'b'.repeat(64),
  output_filename: 'Engagement agreement - revision 1.docx',
  output_sha256: SHA,
  artifact_url: '/api/matters/matter-1/document-revisions/revision-1/artifact',
  version_no: 1,
  instruction: 'Change the retainer to $3,000.',
  status: 'ready_for_review',
  requested_model_tier: 'standard',
  summary: 'Updated the retainer amount.',
  warnings: [],
  operations: [{
    type: 'replace_text',
    block_id: 'paragraph-4',
    target_text: 'Client will pay a $2,500 retainer.',
    replacement_text: 'Client will pay a $3,000 retainer.',
    rationale: 'Updated the requested retainer amount.',
  }],
  output_text_preview: [{
    block_id: 'paragraph-4',
    kind: 'paragraph',
    scope: 'body',
    path: 'document/body/p[4]',
    text: 'Client will pay a $3,000 retainer.',
  }],
}

function CurrentPath() {
  return <output aria-label="Current path">{useLocation().pathname}</output>
}

function renderRevisionPage(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/matters/:matterId/documents/:documentId/revise" element={<><DocumentRevisionPage /><CurrentPath /></>} />
        <Route path="/matters/:matterId/documents/:documentId/revisions/:revisionId" element={<><DocumentRevisionPage /><CurrentPath /></>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('DocumentRevisionPage', () => {
  beforeEach(() => {
    apiMocks.getMatterDocuments.mockResolvedValue({
      items: [{ id: 'root-doc', filename: 'Engagement agreement.docx', content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }],
    })
    apiMocks.listMatterDocumentRevisions.mockResolvedValue({ items: [] })
    apiMocks.getMatterDocumentRevision.mockResolvedValue(readyRevision)
    apiMocks.getMatterDocumentRevisionArtifactUrl.mockImplementation((matterId, revisionId) => `/api/matters/${matterId}/document-revisions/${revisionId}/artifact`)
    apiMocks.approveMatterDocumentRevision.mockResolvedValue({ ...readyRevision, status: 'approved' })
    apiMocks.rejectMatterDocumentRevision.mockResolvedValue({ ...readyRevision, status: 'rejected' })
    apiMocks.listSignatureRequests.mockResolvedValue([])
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('requires exact-artifact acknowledgment and binds approval to the output hash', async () => {
    const user = userEvent.setup()
    renderRevisionPage('/matters/matter-1/documents/root-doc/revisions/revision-1')

    await screen.findByText('Updated the retainer amount.')
    expect(screen.getByText('Client will pay a $2,500 retainer.')).toBeInTheDocument()
    expect(screen.getByText('Client will pay a $3,000 retainer.')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Preview' })[0])

    expect(screen.getByRole('link', { name: 'Open exact DOCX' })).toHaveAttribute('href', readyRevision.artifact_url)
    expect(screen.getByText(/Content preview — not page-faithful/i)).toBeInTheDocument()
    const approveButton = screen.getByRole('button', { name: 'Approve reviewed revision' })
    expect(approveButton).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: /I reviewed the exact DOCX artifact/i }))
    expect(approveButton).toBeEnabled()
    await user.click(approveButton)

    await waitFor(() => {
      expect(apiMocks.approveMatterDocumentRevision).toHaveBeenCalledWith('matter-1', 'revision-1', {
        reviewed_output_sha256: SHA,
      })
    })
  })

  it('creates a follow-up from the current output document and navigates to its durable revision URL', async () => {
    const user = userEvent.setup()
    apiMocks.createMatterDocumentRevision.mockResolvedValue({ id: 'revision-2' })
    apiMocks.getMatterDocumentRevision
      .mockResolvedValueOnce(readyRevision)
      .mockResolvedValueOnce({
        ...readyRevision,
        id: 'revision-2',
        source_document_id: 'output-doc',
        output_document_id: null,
        status: 'needs_input',
        clarification_question: 'Which paragraph should change?',
      })

    renderRevisionPage('/matters/matter-1/documents/root-doc/revisions/revision-1')
    await screen.findByText('Updated the retainer amount.')
    await user.click(screen.getAllByRole('button', { name: 'Request' })[0])
    await user.type(screen.getByLabelText('Document change instructions'), 'Also update the termination period to 30 days.')
    await user.selectOptions(screen.getByLabelText('Model'), 'premium')
    await user.click(screen.getByRole('button', { name: 'Prepare another revision' }))

    await waitFor(() => {
      expect(apiMocks.createMatterDocumentRevision).toHaveBeenCalledWith('matter-1', 'output-doc', {
        instruction: 'Also update the termination period to 30 days.',
        client_request_id: expect.stringMatching(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i),
        model_tier: 'premium',
      })
      expect(screen.getByLabelText('Current path')).toHaveTextContent('/matters/matter-1/documents/output-doc/revisions/revision-2')
    })
  })

  it('only prepares a non-executable internal portal replacement and says nothing was sent', async () => {
    const user = userEvent.setup()
    apiMocks.getMatterDocumentRevision.mockResolvedValue({ ...readyRevision, status: 'approved' })
    apiMocks.listSignatureRequests.mockResolvedValue([{
      id: 'signature-1',
      document_name: 'Original engagement agreement.docx',
      status: 'sent',
      provider: 'internal',
      signers: [{ id: 'signer-1', name: 'Jane Doe', email: 'jane@example.com', status: 'pending' }],
    }])
    apiMocks.prepareMatterDocumentRevisionESignReplacement.mockResolvedValue({
      ...readyRevision,
      status: 'approved',
      prepared_esign_preview: {
        executable: false,
        semantics: 'internal_portal_signature_acknowledgment',
        notification_will_be_sent: false,
        notice: 'Preview only.',
      },
    })

    renderRevisionPage('/matters/matter-1/documents/root-doc/revisions/revision-1')

    expect(await screen.findByText(/not an external e-signature service and does not send an invitation/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Prepare replacement preview' }))

    expect(await screen.findByText('Nothing was voided or sent')).toBeInTheDocument()
    expect(screen.getByText(/Executable: false/i)).toBeInTheDocument()
    expect(apiMocks.prepareMatterDocumentRevisionESignReplacement).toHaveBeenCalledWith('matter-1', 'revision-1', {
      signature_request_id: 'signature-1',
    })
  })

  it('restores a still-valid persisted replacement preview after refresh', async () => {
    apiMocks.getMatterDocumentRevision.mockResolvedValue({
      ...readyRevision,
      status: 'approved',
      prepared_esign_preview: {
        executable: false,
        semantics: 'internal_portal_signature_acknowledgment',
        notification_will_be_sent: false,
        notice: 'Preview only.',
      },
    })
    apiMocks.listSignatureRequests.mockResolvedValue([])

    renderRevisionPage('/matters/matter-1/documents/root-doc/revisions/revision-1')

    expect(await screen.findByText('Nothing was voided or sent')).toBeInTheDocument()
    expect(screen.getByText(/Executable: false/i)).toBeInTheDocument()
  })

  it('shows a superseded revision as read-only', async () => {
    const user = userEvent.setup()
    apiMocks.getMatterDocumentRevision.mockResolvedValue({ ...readyRevision, status: 'superseded' })

    renderRevisionPage('/matters/matter-1/documents/root-doc/revisions/revision-1')

    await screen.findByText('Updated the retainer amount.')
    await user.click(screen.getAllByRole('button', { name: 'Preview' })[0])
    expect(await screen.findByText('Revision superseded')).toBeInTheDocument()
    expect(screen.getByText(/can no longer be approved/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve reviewed revision' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reject revision' })).not.toBeInTheDocument()
  })

  it('has no detectable accessibility violations in the full review state', async () => {
    const user = userEvent.setup()
    const { container } = renderRevisionPage('/matters/matter-1/documents/root-doc/revisions/revision-1')
    await screen.findByText('Updated the retainer amount.')
    await user.click(screen.getAllByRole('button', { name: 'Preview' })[0])
    expect(await axe(container)).toHaveNoViolations()
  })
})
