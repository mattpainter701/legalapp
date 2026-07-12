import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TemplatesPage from './TemplatesPage'
import {
  analyzeTemplateUpload,
  createTemplate,
  createTemplateFromUpload,
  getTemplates,
  renderTemplate,
  renderTemplateFile,
  updateTemplate,
} from '../api'

const originalCreateObjectURL = URL.createObjectURL
const originalRevokeObjectURL = URL.revokeObjectURL

vi.mock('../api', () => ({
  getTemplates: vi.fn().mockResolvedValue({ items: [{ id: 'template-1', title: 'Engagement Letter', body: 'Dear {{client_name}}', category: 'engagement_letter', is_active: true }] }),
  getMattersV2: vi.fn().mockResolvedValue({ items: [{ id: 'matter-1', matter_name: 'Smith Matter', client_name: 'Smith' }] }),
  analyzeTemplateUpload: vi.fn(),
  createTemplate: vi.fn().mockResolvedValue({}),
  createTemplateFromUpload: vi.fn().mockResolvedValue({}),
  updateTemplate: vi.fn(),
  deleteTemplate: vi.fn(),
  renderTemplate: vi.fn(),
  renderTemplateFile: vi.fn(),
  discoverTemplateVariables: vi.fn(),
  getMatterDocumentDownloadUrl: (matterId, documentId) => `/api/matters/${matterId}/documents/${documentId}/download`,
  triggerBlobDownload: vi.fn(),
}))

describe('document template workflow', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => {
    cleanup()
    URL.createObjectURL = originalCreateObjectURL
    URL.revokeObjectURL = originalRevokeObjectURL
  })

  it('keeps preview side-effect free and persists only the explicit save render', async () => {
    renderTemplate
      .mockResolvedValueOnce({ rendered: 'Dear Jane' })
      .mockResolvedValueOnce({
        rendered: 'Dear Jane',
        matter_document_id: 'document-1',
        storage_backend: 'local',
        storage_warning: 'Configured Google Drive storage was unavailable. The document was saved locally; reconnect Google Drive or verify its permissions.',
      })
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Generate' }))
    await user.click(screen.getByRole('button', { name: /Smith Matter/ }))
    await user.type(screen.getByPlaceholderText('Enter Client Name'), 'Jane')
    await user.click(screen.getByRole('button', { name: 'Preview' }))

    expect(renderTemplate).toHaveBeenNthCalledWith(1, 'template-1', expect.objectContaining({ matter_id: null }))
    expect(screen.queryByText(/Saved to the matter/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Render & Save to Matter' }))
    await waitFor(() => expect(renderTemplate).toHaveBeenCalledTimes(2))
    expect(renderTemplate).toHaveBeenNthCalledWith(2, 'template-1', expect.objectContaining({ matter_id: 'matter-1' }))
    expect(await screen.findByRole('link', { name: /Download saved document/ })).toHaveAttribute('href', '/api/matters/matter-1/documents/document-1/download')
    expect(screen.getByText(/saved locally; reconnect Google Drive/i)).toBeInTheDocument()
  })

  it('shows only working document automation tabs', async () => {
    render(<TemplatesPage />)

    expect(await screen.findByRole('button', { name: 'Templates' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Generate / Smart Fill' }))
    expect(screen.getByRole('heading', { name: 'PDF template checklist' })).toBeInTheDocument()
    expect(screen.getByText(/standard fillable AcroForm PDF/)).toBeInTheDocument()
    expect(screen.queryByText('Integration Hooks')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'E-Sign Queue' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approvals' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Branding / Settings' })).not.toBeInTheDocument()
  })

  it('traps create-modal focus, closes on Escape, and restores the trigger', async () => {
    const user = userEvent.setup()
    render(<TemplatesPage />)

    const trigger = await screen.findByRole('button', { name: 'New Template' })
    await user.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'Create Template' })
    const title = within(dialog).getByRole('textbox', { name: 'Title' })
    await waitFor(() => expect(title).toHaveFocus())

    const close = within(dialog).getByRole('button', { name: 'Close' })
    close.focus()
    await user.tab({ shift: true })
    expect(within(dialog).getByRole('button', { name: 'Create' })).toHaveFocus()
    await user.tab()
    expect(close).toHaveFocus()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Create Template' })).not.toBeInTheDocument())
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('gives delete confirmation alert semantics and complete keyboard behavior', async () => {
    const user = userEvent.setup()
    render(<TemplatesPage />)

    const trigger = await screen.findByRole('button', { name: 'Delete' })
    await user.click(trigger)
    const dialog = screen.getByRole('alertdialog', { name: 'Delete template?' })
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' })
    await waitFor(() => expect(cancel).toHaveFocus())

    await user.tab({ shift: true })
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toHaveFocus()
    await user.tab()
    expect(cancel).toHaveFocus()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('analyzes a PDF before creation and sends the original source through multipart upload', async () => {
    analyzeTemplateUpload.mockResolvedValue({
      title: 'Court Form',
      format: 'pdf',
      body: 'Name: {{client_name}}',
      suggested_variable_schema: { fields: [{
        name: 'client_name', label: 'Client name', pdf_field_name: 'ClientName',
        field_type: 'choice', page: 2, required: true,
        options: [{ value: 'state', label: 'State Court' }, { value: 'federal', label: 'Federal Court' }],
      }] },
      detected_branding_profile: {},
      warnings: [],
    })
    URL.createObjectURL = vi.fn().mockReturnValue('blob:validated-source')
    URL.revokeObjectURL = vi.fn()
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Upload Sample' }))
    expect(screen.getByText(/PDFs must already contain fillable AcroForm fields/)).toBeInTheDocument()
    const file = new File(['%PDF-1.7'], 'court-form.pdf', { type: 'application/pdf' })
    fireEvent.change(await screen.findByLabelText('Sample document'), { target: { files: [file] } })
    expect(screen.queryByTitle('Source PDF preview: court-form.pdf')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Analyze sample' }))
    await screen.findByDisplayValue('Court Form')
    expect(screen.getByTitle('Source PDF preview: court-form.pdf')).toHaveAttribute('data', 'blob:validated-source')
    expect(URL.createObjectURL).toHaveBeenCalledWith(file)
    expect(screen.getByLabelText('PDF metadata for Client name')).toHaveTextContent('choice')
    expect(screen.getByLabelText('PDF metadata for Client name')).toHaveTextContent('Page 2')
    expect(screen.getByLabelText('PDF metadata for Client name')).toHaveTextContent('Required')
    expect(screen.getByText('Options:').closest('p')).toHaveTextContent('Options: State Court, Federal Court')
    const mappedField = screen.getByLabelText('Client name')
    fireEvent.change(mappedField, { target: { value: 'party_name' } })
    await user.click(screen.getByRole('button', { name: 'Create reviewed template' }))

    await waitFor(() => expect({ upload: createTemplateFromUpload.mock.calls.length, json: createTemplate.mock.calls.length }).toEqual({ upload: 1, json: 0 }))
    expect(createTemplate).not.toHaveBeenCalled()
    const form = createTemplateFromUpload.mock.calls[0][0]
    expect(form.get('file')).toEqual(file)
    expect(form.get('category')).toBe('other')
    expect(form.get('reviewed_body')).toBe('Name: {{party_name}}')
    expect(JSON.parse(form.get('variable_schema'))).toEqual(expect.objectContaining({
      fields: [expect.objectContaining({ name: 'party_name', pdf_field_name: 'ClientName' })],
    }))
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:validated-source'))
  })

  it('replaces and revokes validated source previews when the upload changes', async () => {
    analyzeTemplateUpload.mockResolvedValue({
      title: 'Validated Form',
      format: 'pdf',
      body: 'Name: {{client_name}}',
      suggested_variable_schema: { fields: [{ name: 'client_name', label: 'Client name', pdf_field_name: 'ClientName', field_type: 'text', page: 1 }] },
      detected_branding_profile: {},
      warnings: [],
    })
    URL.createObjectURL = vi.fn()
      .mockReturnValueOnce('blob:first-validated-source')
      .mockReturnValueOnce('blob:second-validated-source')
    URL.revokeObjectURL = vi.fn()
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Upload Sample' }))
    const input = screen.getByLabelText('Sample document')
    const first = new File(['%PDF-1.7 first'], 'first.pdf', { type: 'application/pdf' })
    fireEvent.change(input, { target: { files: [first] } })
    await user.click(screen.getByRole('button', { name: 'Analyze sample' }))
    expect(await screen.findByTitle('Source PDF preview: first.pdf')).toHaveAttribute('data', 'blob:first-validated-source')

    const second = new File(['%PDF-1.7 second'], 'second.pdf', { type: 'application/pdf' })
    fireEvent.change(input, { target: { files: [second] } })
    await waitFor(() => expect(screen.queryByTitle('Source PDF preview: first.pdf')).not.toBeInTheDocument())
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:first-validated-source')

    await user.click(screen.getByRole('button', { name: 'Analyze sample' }))
    expect(await screen.findByTitle('Source PDF preview: second.pdf')).toHaveAttribute('data', 'blob:second-validated-source')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:second-validated-source'))
  })

  it('uses the binary endpoint for a side-effect-free PDF preview', async () => {
    getTemplates.mockResolvedValueOnce({ items: [{ id: 'pdf-template', title: 'Court Form', body: 'Name: {{client_name}}', category: 'other', format: 'pdf', source_filename: 'court.pdf', source_sha256: 'abc', is_active: true }] })
    const pdfBlob = new Blob(['%PDF-1.7'], { type: 'application/pdf' })
    renderTemplateFile.mockResolvedValue({ blob: pdfBlob, filename: 'Court_Form.pdf', contentType: 'application/pdf' })
    URL.createObjectURL = vi.fn().mockReturnValue('blob:pdf-preview')
    URL.revokeObjectURL = vi.fn()
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Generate' }))
    await user.click(screen.getByRole('button', { name: 'Preview' }))

    await waitFor(() => expect(renderTemplateFile).toHaveBeenCalledWith('pdf-template', { variables: { client_name: '' }, matter_id: null }))
    expect(renderTemplate).not.toHaveBeenCalled()
    expect(screen.getByTitle('Preview of Court Form')).toHaveAttribute('data', 'blob:pdf-preview')
    expect(screen.getByRole('button', { name: 'Download preview' })).toBeInTheDocument()
    expect(URL.createObjectURL).toHaveBeenCalledWith(pdfBlob)

    await user.type(screen.getByPlaceholderText('Enter Client Name'), 'Ada')
    await waitFor(() => expect(screen.queryByTitle('Preview of Court Form')).not.toBeInTheDocument())
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:pdf-preview')

  })

  it('surfaces an actionable PDF render error', async () => {
    getTemplates.mockResolvedValueOnce({ items: [{ id: 'pdf-template', title: 'Flat Scan', body: '', category: 'other', format: 'pdf', source_filename: 'scan.pdf', source_sha256: 'abc', is_active: true, variable_schema: { fields: [] } }] })
    const error = new Error('This PDF has no fillable AcroForm fields.')
    error.response = { status: 422, data: { detail: error.message } }
    renderTemplateFile.mockRejectedValue(error)
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Generate' }))
    await user.click(screen.getByRole('button', { name: 'Preview' }))

    expect(await screen.findByText('This PDF has no fillable AcroForm fields.')).toBeInTheDocument()
  })

  it('renders PDF fields from schema metadata and blocks only missing required values', async () => {
    getTemplates.mockResolvedValueOnce({ items: [{
      id: 'schema-pdf', title: 'Schema Form', body: 'For {{client_name}}', category: 'other', format: 'pdf',
      source_filename: 'schema.pdf', source_sha256: 'abc', is_active: true,
      variable_schema: { fields: [
        { name: 'client_name', label: 'Client name', pdf_field_name: 'ClientName', field_type: 'text', required: true },
        { name: 'approved', label: 'Approved', pdf_field_name: 'Approved', field_type: 'checkbox', required: false },
        { name: 'venue', label: 'Venue', pdf_field_name: 'Venue', field_type: 'choice', required: true, options: ['Cook County', 'Lake County'] },
        { name: 'notes', label: 'Notes', pdf_field_name: 'Notes', field_type: 'text', multiline: true, required: false },
        { name: 'signature_1', label: 'Client signature', pdf_field_name: 'Signature1', field_type: 'signature', required: true },
      ] },
    }] })
    renderTemplate.mockResolvedValue({ rendered: '', matter_document_id: 'document-2', output_format: 'pdf' })
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Generate' }))
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(screen.getByRole('combobox', { name: /Venue/ })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /Notes/ }).tagName).toBe('TEXTAREA')
    expect(screen.getByText(/Signature area is left blank for signing/)).toBeInTheDocument()
    expect(screen.getByText(/2 required fields still need review/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Smith Matter/ }))
    await user.type(screen.getByRole('textbox', { name: /Client name/ }), 'Jane')
    await user.selectOptions(screen.getByRole('combobox', { name: /Venue/ }), 'Cook County')
    expect(screen.getByText(/1 optional field left unfilled/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Render & Save to Matter' }))

    await waitFor(() => expect(renderTemplate).toHaveBeenCalledWith('schema-pdf', {
      matter_id: 'matter-1',
      variables: { client_name: 'Jane', approved: 'false', venue: 'Cook County', notes: '' },
    }))
  })

  it('flags migrated PDF records without source metadata and disables generation', async () => {
    getTemplates.mockResolvedValueOnce({ items: [{ id: 'legacy-pdf', title: 'Legacy PDF', body: '', category: 'other', format: 'pdf', is_active: true }] })
    render(<TemplatesPage />)

    expect(await screen.findByText('Source missing — recreate this PDF template')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Deactivate template' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: 'Recreate from Upload Sample' }))
    expect(screen.getByRole('heading', { name: 'Create Template From Sample' })).toBeInTheDocument()
  })

  it('previews drafts but prevents saving them to a matter', async () => {
    getTemplates.mockResolvedValueOnce({ items: [{ id: 'draft-template', title: 'Draft Letter', body: 'Dear {{client_name}}', category: 'other', is_active: false }] })
    renderTemplate.mockResolvedValue({ rendered: 'Dear Jane' })
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Preview draft' }))
    expect(screen.getByText(/This template is inactive/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Smith Matter/ }))
    expect(screen.getByRole('button', { name: 'Render & Save to Matter' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Preview' }))
    await waitFor(() => expect(renderTemplate).toHaveBeenCalledWith('draft-template', expect.objectContaining({ matter_id: null })))

    await user.click(screen.getByRole('button', { name: 'Close' }))
    await user.click(screen.getByRole('button', { name: 'Generate / Smart Fill' }))
    expect(screen.getByText('Activate a verified template before generating matter documents.')).toBeInTheDocument()
  })

  it('edits only PDF metadata instead of presenting a non-functional body editor', async () => {
    getTemplates.mockResolvedValueOnce({ items: [{
      id: 'pdf-template', title: 'Court Form', body: 'ignored extracted text', category: 'other', format: 'pdf',
      source_filename: 'court.pdf', source_sha256: 'abc', is_active: true,
    }] })
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    const editDialog = screen.getByRole('dialog', { name: 'Edit Template' })
    expect(screen.queryByRole('textbox', { name: 'Body' })).not.toBeInTheDocument()
    expect(screen.getByText(/PDF layout and field mappings come from the source file/)).toBeInTheDocument()
    const title = within(editDialog).getByRole('textbox', { name: 'Title' })
    await waitFor(() => expect(title).toHaveFocus())
    await user.clear(title)
    await user.type(title, 'Verified Court Form')
    await user.click(screen.getByRole('button', { name: 'Update' }))

    await waitFor(() => expect(updateTemplate).toHaveBeenCalledWith('pdf-template', {
      title: 'Verified Court Form',
      category: 'other',
    }))
  })

  it('prevents creating a PDF analysis with no AcroForm mappings', async () => {
    analyzeTemplateUpload.mockResolvedValue({
      title: 'Flat PDF', format: 'pdf', body: 'Extracted text',
      suggested_variable_schema: { fields: [] }, detected_branding_profile: {}, warnings: [],
    })
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Upload Sample' }))
    fireEvent.change(await screen.findByLabelText('Sample document'), {
      target: { files: [new File(['%PDF-1.7'], 'flat.pdf', { type: 'application/pdf' })] },
    })
    await user.click(screen.getByRole('button', { name: 'Analyze sample' }))

    expect(await screen.findByText(/cannot be created as a generation template/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create reviewed template' })).toBeDisabled()
  })
})
