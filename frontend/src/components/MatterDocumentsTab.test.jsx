import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MatterDocumentsTab, { canReviseWithAssistant, isAssistantRevisionDocument } from './MatterDocumentsTab'
import { ConfirmProvider } from './dialog/ConfirmProvider'
import { ToastProvider } from './toast/ToastProvider'

const apiMocks = vi.hoisted(() => ({
  deleteMatterDocument: vi.fn(),
  getMatterCloudFiles: vi.fn(),
  getMatterCloudFolder: vi.fn(),
  getMatterDocumentDownloadUrl: vi.fn(),
  getMatterDocuments: vi.fn(),
  provisionMatterCloudFolder: vi.fn(),
  syncMatterCloudFolder: vi.fn(),
  updateMatterDocument: vi.fn(),
  uploadMatterDocument: vi.fn(),
}))

vi.mock('../api', () => apiMocks)

const documents = [
  {
    id: 'docx-1',
    filename: 'Contract.docx',
    content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    document_category: 'contract',
    file_size: 2048,
    description: 'Client engagement terms',
    portal_visible: false,
    storage_backend: 'local',
    created_at: '2026-08-04T12:00:00Z',
  },
  {
    id: 'pdf-1',
    filename: 'Filed pleading.pdf',
    content_type: 'application/pdf',
    document_category: 'pleading',
    file_size: 4096,
    portal_visible: false,
    storage_backend: 'local',
    created_at: '2026-08-03T12:00:00Z',
  },
  {
    id: 'assistant-1',
    filename: 'Contract-revision-v1.docx',
    content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    document_category: 'assistant_revision',
    file_size: 2200,
    portal_visible: false,
    storage_backend: 'local',
    created_at: '2026-08-04T13:00:00Z',
  },
]

function renderDocuments(onReviseDocument = vi.fn()) {
  return {
    onReviseDocument,
    ...render(
      <ToastProvider>
        <ConfirmProvider>
          <MatterDocumentsTab matterId="matter-1" onReviseDocument={onReviseDocument} />
        </ConfirmProvider>
      </ToastProvider>,
    ),
  }
}

describe('MatterDocumentsTab assistant revision entry point', () => {
  beforeEach(() => {
    apiMocks.getMatterDocuments.mockResolvedValue({ items: documents })
    apiMocks.getMatterCloudFiles.mockResolvedValue({ files: [] })
    apiMocks.getMatterCloudFolder.mockResolvedValue(null)
    apiMocks.getMatterDocumentDownloadUrl.mockImplementation((matterId, documentId) => `/api/matters/${matterId}/documents/${documentId}/download`)
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('recognizes DOCX by extension or MIME type but not PDF', () => {
    expect(canReviseWithAssistant({ filename: 'draft.DOCX' })).toBe(true)
    expect(canReviseWithAssistant({ filename: 'draft', mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })).toBe(true)
    expect(canReviseWithAssistant({ filename: 'file.pdf', content_type: 'application/pdf' })).toBe(false)
    expect(isAssistantRevisionDocument(documents[2])).toBe(true)
  })

  it('opens the assistant for DOCX and disables other formats with a clear reason', async () => {
    const user = userEvent.setup()
    const { onReviseDocument } = renderDocuments()

    const docxButtons = await screen.findAllByRole('button', { name: 'Revise Contract.docx with assistant' })
    await user.click(docxButtons[0])
    expect(onReviseDocument).toHaveBeenCalledWith(expect.objectContaining({ id: 'docx-1' }))

    const pdfButtons = screen.getAllByRole('button', { name: 'Revise Filed pleading.pdf with assistant' })
    expect(pdfButtons.length).toBeGreaterThan(0)
    pdfButtons.forEach((button) => expect(button).toBeDisabled())
    expect(screen.getByText('Assistant revisions currently support DOCX files only.')).toBeInTheDocument()
  })

  it('has no detectable accessibility violations in the mobile and desktop document layouts', async () => {
    const { container } = renderDocuments()
    await screen.findAllByText('Contract.docx')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('keeps assistant derivatives out of the legacy client-release control', async () => {
    renderDocuments()

    const releaseControls = await screen.findAllByRole('button', {
      name: 'Contract-revision-v1.docx requires a separate release workflow',
    })
    releaseControls.forEach((control) => expect(control).toBeDisabled())
    expect(screen.getAllByText('Release locked').length).toBeGreaterThan(0)
    expect(apiMocks.updateMatterDocument).not.toHaveBeenCalled()
  })
})
