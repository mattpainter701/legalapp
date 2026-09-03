import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MatterDocumentsTab, { canReviseWithAssistant, isAssistantRevisionDocument } from './MatterDocumentsTab'
import { ConfirmProvider } from './dialog/ConfirmProvider'
import { ToastProvider } from './toast/ToastProvider'

const apiMocks = vi.hoisted(() => ({
  createDocumentTag: vi.fn(),
  createMatterDocumentFolder: vi.fn(),
  deleteMatterDocument: vi.fn(),
  deleteMatterDocumentFolder: vi.fn(),
  getDocumentTags: vi.fn(),
  getMatterCloudFiles: vi.fn(),
  getMatterCloudFolder: vi.fn(),
  getMatterDocumentDownloadUrl: vi.fn(),
  getMatterDocumentFolders: vi.fn(),
  getMatterDocuments: vi.fn(),
  moveMatterDocuments: vi.fn(),
  provisionMatterCloudFolder: vi.fn(),
  setMatterDocumentTags: vi.fn(),
  syncMatterCloudFolder: vi.fn(),
  updateMatterDocument: vi.fn(),
  updateMatterDocumentFolder: vi.fn(),
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

const folders = [
  {
    id: 'folder-discovery',
    matter_id: 'matter-1',
    parent_id: null,
    name: 'Discovery',
    path: 'Discovery',
    depth: 0,
    kind: 'user',
    system_key: null,
    document_count: 1,
    created_at: '2026-08-01T12:00:00Z',
    updated_at: '2026-08-01T12:00:00Z',
  },
  {
    id: 'folder-depos',
    matter_id: 'matter-1',
    parent_id: 'folder-discovery',
    name: 'Depositions',
    path: 'Discovery/Depositions',
    depth: 1,
    kind: 'user',
    system_key: null,
    document_count: 0,
    created_at: '2026-08-01T12:00:00Z',
    updated_at: '2026-08-01T12:00:00Z',
  },
  {
    id: 'folder-client-uploads',
    matter_id: 'matter-1',
    parent_id: null,
    name: 'Client Uploads',
    path: 'Client Uploads',
    depth: 0,
    kind: 'system',
    system_key: 'client_uploads',
    document_count: 1,
    created_at: '2026-08-01T12:00:00Z',
    updated_at: '2026-08-01T12:00:00Z',
  },
]

const tags = [
  { id: 'tag-signed', name: 'Signed', color: 'green' },
  { id: 'tag-privileged', name: 'Privileged', color: 'rose' },
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
    apiMocks.getMatterDocuments.mockResolvedValue({ items: documents, total: documents.length })
    apiMocks.getMatterCloudFiles.mockResolvedValue({ files: [] })
    apiMocks.getMatterCloudFolder.mockResolvedValue(null)
    apiMocks.getMatterDocumentFolders.mockResolvedValue({ items: folders, total: folders.length, root_document_count: 1 })
    apiMocks.getDocumentTags.mockResolvedValue({ items: tags, total: tags.length })
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


describe('MatterDocumentsTab document explorer', () => {
  beforeEach(() => {
    apiMocks.getMatterDocuments.mockResolvedValue({ items: documents, total: documents.length })
    apiMocks.getMatterCloudFiles.mockResolvedValue({ files: [] })
    apiMocks.getMatterCloudFolder.mockResolvedValue(null)
    apiMocks.getMatterDocumentFolders.mockResolvedValue({ items: folders, total: folders.length, root_document_count: 1 })
    apiMocks.getDocumentTags.mockResolvedValue({ items: tags, total: tags.length })
    apiMocks.getMatterDocumentDownloadUrl.mockImplementation((matterId, documentId) => `/api/matters/${matterId}/documents/${documentId}/download`)
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('renders the folder rail with counts and marks the system folder unmanageable', async () => {
    renderDocuments()

    const rail = await screen.findByRole('navigation', { name: 'Document folders' })
    expect(within(rail).getByRole('button', { name: 'Discovery' })).toBeInTheDocument()
    expect(within(rail).getByRole('button', { name: 'Unfiled' })).toBeInTheDocument()

    // A firm can add a subfolder under the protected folder but cannot rename
    // or delete it, because the client portal files uploads there.
    expect(
      within(rail).getByRole('button', { name: 'New subfolder in Client Uploads' }),
    ).toBeInTheDocument()
    expect(
      within(rail).queryByRole('button', { name: 'Rename Client Uploads' }),
    ).not.toBeInTheDocument()
    expect(
      within(rail).queryByRole('button', { name: 'Delete Client Uploads' }),
    ).not.toBeInTheDocument()
  })

  it('scopes the listing to the folder the user opens', async () => {
    const user = userEvent.setup()
    renderDocuments()

    const rail = await screen.findByRole('navigation', { name: 'Document folders' })
    await user.click(within(rail).getByRole('button', { name: 'Discovery' }))

    await waitFor(() =>
      expect(apiMocks.getMatterDocuments).toHaveBeenLastCalledWith(
        'matter-1',
        expect.objectContaining({ folder_id: 'folder-discovery', include_subfolders: true }),
      ),
    )
  })

  it('asks the server for unfiled documents rather than filtering in the browser', async () => {
    const user = userEvent.setup()
    renderDocuments()

    const rail = await screen.findByRole('navigation', { name: 'Document folders' })
    await user.click(within(rail).getByRole('button', { name: 'Unfiled' }))

    await waitFor(() =>
      expect(apiMocks.getMatterDocuments).toHaveBeenLastCalledWith(
        'matter-1',
        expect.objectContaining({ folder_id: 'root' }),
      ),
    )
  })

  it('creates a folder and opens it', async () => {
    const user = userEvent.setup()
    apiMocks.createMatterDocumentFolder.mockResolvedValue({
      id: 'folder-new',
      name: 'Trial',
      path: 'Trial',
      parent_id: null,
      depth: 0,
      kind: 'user',
      document_count: 0,
    })
    renderDocuments()

    await screen.findByRole('navigation', { name: 'Document folders' })
    await user.click(screen.getByRole('button', { name: /New Folder/i }))
    await user.type(screen.getByLabelText('New folder name'), 'Trial')
    await user.click(screen.getByRole('button', { name: 'Create folder' }))

    await waitFor(() =>
      expect(apiMocks.createMatterDocumentFolder).toHaveBeenCalledWith('matter-1', {
        name: 'Trial',
        parent_id: null,
      }),
    )
    await waitFor(() =>
      expect(apiMocks.getMatterDocuments).toHaveBeenLastCalledWith(
        'matter-1',
        expect.objectContaining({ folder_id: 'folder-new' }),
      ),
    )
  })

  it('files a document into a folder when it is dragged onto the rail', async () => {
    apiMocks.moveMatterDocuments.mockResolvedValue({ moved: 1, folder_id: 'folder-discovery', items: [] })
    renderDocuments()

    const rail = await screen.findByRole('navigation', { name: 'Document folders' })
    const target = within(rail).getByRole('button', { name: 'Discovery' }).closest('div')
    const payload = JSON.stringify(['docx-1'])
    const dataTransfer = {
      getData: (type) => (type === 'text/plain' || type.startsWith('application/') ? payload : ''),
      dropEffect: '',
    }

    fireEvent.dragOver(target, { dataTransfer })
    fireEvent.drop(target, { dataTransfer })

    await waitFor(() =>
      expect(apiMocks.moveMatterDocuments).toHaveBeenCalledWith(
        'matter-1',
        ['docx-1'],
        'folder-discovery',
      ),
    )
  })

  it('searches and filters by tag on the server', async () => {
    const user = userEvent.setup()
    renderDocuments()

    await screen.findByRole('navigation', { name: 'Document folders' })
    await user.type(screen.getByLabelText('Search documents'), 'compel')
    await waitFor(() =>
      expect(apiMocks.getMatterDocuments).toHaveBeenLastCalledWith(
        'matter-1',
        expect.objectContaining({ q: 'compel' }),
      ),
    )

    await user.click(screen.getByRole('button', { name: 'Signed', pressed: false }))
    await waitFor(() =>
      expect(apiMocks.getMatterDocuments).toHaveBeenLastCalledWith(
        'matter-1',
        expect.objectContaining({ tag_ids: ['tag-signed'] }),
      ),
    )
  })

  it('replaces the tags on a document from its row', async () => {
    const user = userEvent.setup()
    apiMocks.setMatterDocumentTags.mockResolvedValue({ items: [tags[0]], total: 1 })
    renderDocuments()

    await screen.findByRole('navigation', { name: 'Document folders' })
    await user.click(screen.getByRole('button', { name: 'Edit tags for Contract.docx' }))

    const dialog = await screen.findByRole('dialog', { name: 'Edit document tags' })
    await user.click(within(dialog).getByRole('checkbox', { name: /Signed/ }))
    await user.click(within(dialog).getByRole('button', { name: 'Save tags' }))

    await waitFor(() =>
      expect(apiMocks.setMatterDocumentTags).toHaveBeenCalledWith('matter-1', 'docx-1', [
        'tag-signed',
      ]),
    )
  })

  it('uploads into the folder that is currently open', async () => {
    const user = userEvent.setup()
    apiMocks.uploadMatterDocument.mockResolvedValue({ ...documents[0], id: 'new-doc' })
    renderDocuments()

    const rail = await screen.findByRole('navigation', { name: 'Document folders' })
    await user.click(within(rail).getByRole('button', { name: 'Discovery' }))
    await user.click(screen.getByRole('button', { name: /Upload Document/i }))

    await waitFor(() =>
      expect(screen.getByLabelText('Folder')).toHaveValue('folder-discovery'),
    )

    const file = new File(['body'], 'rogs.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText('File *'), file)
    await user.click(screen.getByRole('button', { name: /^Upload$/ }))

    await waitFor(() => expect(apiMocks.uploadMatterDocument).toHaveBeenCalled())
    const formData = apiMocks.uploadMatterDocument.mock.calls[0][1]
    expect(formData.get('folder_id')).toBe('folder-discovery')
  })

  it('deletes a folder without deleting the documents inside it', async () => {
    const user = userEvent.setup()
    apiMocks.deleteMatterDocumentFolder.mockResolvedValue({
      deleted_folder_id: 'folder-discovery',
      documents_moved: 1,
      moved_to_folder_id: null,
    })
    renderDocuments()

    const rail = await screen.findByRole('navigation', { name: 'Document folders' })
    await user.click(within(rail).getByRole('button', { name: 'Delete Discovery' }))
    await user.click(await screen.findByRole('button', { name: 'Delete folder' }))

    await waitFor(() =>
      expect(apiMocks.deleteMatterDocumentFolder).toHaveBeenCalledWith(
        'matter-1',
        'folder-discovery',
        { moveDocumentsToParent: true },
      ),
    )
  })
})
