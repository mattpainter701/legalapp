import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TemplatesPage from './TemplatesPage'
import {
  analyzeTemplateUpload,
  createTemplate,
  createTemplateFromUpload,
  discoverTemplateVariables,
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
    expect(screen.getByRole('heading', { name: 'Reliable template workflow' })).toBeInTheDocument()
    expect(screen.getByText(/Word or PDF document your team already uses/)).toBeInTheDocument()
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
    expect(screen.getByText(/image-only scans while preserving the original design/)).toBeInTheDocument()
    const file = new File(['%PDF-1.7'], 'court-form.pdf', { type: 'application/pdf' })
    fireEvent.change(await screen.findByLabelText('Sample document'), { target: { files: [file] } })
    expect(screen.queryByTitle('Source PDF preview: court-form.pdf')).not.toBeInTheDocument()
    await screen.findByDisplayValue('Court Form')
    expect(screen.getByTitle('Source PDF preview: court-form.pdf')).toHaveAttribute('data', 'blob:validated-source')
    expect(URL.createObjectURL).toHaveBeenCalledWith(file)
    expect(screen.getByLabelText('PDF metadata for Client name')).toHaveTextContent('choice')
    expect(screen.getByLabelText('PDF metadata for Client name')).toHaveTextContent('Page 2')
    expect(screen.getByLabelText('PDF metadata for Client name')).toHaveTextContent('Required')
    expect(screen.getByText('Options:').closest('p')).toHaveTextContent('Options: State Court, Federal Court')
    const mappedField = screen.getByLabelText('Automation key')
    fireEvent.change(mappedField, { target: { value: 'party_name' } })
    await user.click(screen.getByRole('button', { name: 'Save reusable template' }))

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
    expect(await screen.findByTitle('Source PDF preview: first.pdf')).toHaveAttribute('data', 'blob:first-validated-source')

    const second = new File(['%PDF-1.7 second'], 'second.pdf', { type: 'application/pdf' })
    fireEvent.change(input, { target: { files: [second] } })
    await waitFor(() => expect(screen.queryByTitle('Source PDF preview: first.pdf')).not.toBeInTheDocument())
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:first-validated-source')

    expect(await screen.findByTitle('Source PDF preview: second.pdf')).toHaveAttribute('data', 'blob:second-validated-source')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:second-validated-source'))
  })

  it('keeps DOCX uploads source-backed and clears the previous document analysis', async () => {
    analyzeTemplateUpload
      .mockResolvedValueOnce({
        title: 'First Letter',
        format: 'docx',
        body: 'Dear {{client_name}}',
        suggested_variable_schema: { source: 'docx_source', fields: [{ name: 'client_name', label: 'Client name', source_text: 'Ada Lovelace' }] },
        detected_branding_profile: {},
        warnings: [],
      })
      .mockResolvedValueOnce({
        title: 'Second Letter',
        format: 'docx',
        body: 'Case No. {{case_number}}',
        suggested_variable_schema: { source: 'docx_source', fields: [{ name: 'case_number', label: 'Case number', source_text: 'CV-2026-42' }] },
        detected_branding_profile: {},
        warnings: [],
      })
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Upload Sample' }))
    const input = screen.getByLabelText('Sample document')
    const first = new File(['first'], 'first.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    fireEvent.change(input, { target: { files: [first] } })
    expect(await screen.findByDisplayValue('First Letter')).toBeInTheDocument()
    expect(screen.getByText(/Original Word document preserved/)).toBeInTheDocument()

    const second = new File(['second'], 'second.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    fireEvent.change(input, { target: { files: [second] } })
    expect(screen.queryByDisplayValue('First Letter')).not.toBeInTheDocument()
    expect(screen.getByText(/Current source: second.docx/)).toHaveTextContent('reading now')
    expect(await screen.findByDisplayValue('Second Letter')).toBeInTheDocument()
    expect(analyzeTemplateUpload.mock.calls[1][0].get('title')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Save reusable template' }))

    await waitFor(() => expect(createTemplateFromUpload).toHaveBeenCalledTimes(1))
    const form = createTemplateFromUpload.mock.calls[0][0]
    expect(form.get('file')).toEqual(second)
    expect(form.get('reviewed_body')).toBe('Case No. {{case_number}}')
    expect(createTemplate).not.toHaveBeenCalled()
  })

  it('ignores an older analysis response after the selected source changes', async () => {
    let resolveFirst
    let resolveSecond
    analyzeTemplateUpload
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve }))
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Upload Sample' }))
    const input = screen.getByLabelText('Sample document')
    const first = new File(['first'], 'first.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    const second = new File(['second'], 'second.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    fireEvent.change(input, { target: { files: [first] } })
    fireEvent.change(input, { target: { files: [second] } })

    await act(async () => {
      resolveSecond({
        title: 'Second Source', format: 'docx', body: 'Dear {{client_name}}',
        suggested_variable_schema: { fields: [{ name: 'client_name', label: 'Client name', source_text: 'Ada' }] },
        detected_branding_profile: {}, warnings: [],
      })
      await Promise.resolve()
    })
    expect(await screen.findByDisplayValue('Second Source')).toBeInTheDocument()

    await act(async () => {
      resolveFirst({
        title: 'Stale First Source', format: 'docx', body: 'Old {{matter_name}}',
        suggested_variable_schema: { fields: [{ name: 'matter_name', label: 'Matter name', source_text: 'Old' }] },
        detected_branding_profile: {}, warnings: [],
      })
      await Promise.resolve()
    })
    expect(screen.getByDisplayValue('Second Source')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('Stale First Source')).not.toBeInTheDocument()
    expect(screen.getByText(/Current source: second.docx/)).toHaveTextContent('ready to review')
  })

  it('lets a reviewer mark an undetected Word value as a replacement field', async () => {
    analyzeTemplateUpload.mockResolvedValue({
      title: 'Application',
      format: 'docx',
      body: 'Applicant: Ada Lovelace',
      suggested_variable_schema: { source: 'docx_source', fields: [] },
      detected_branding_profile: {},
      warnings: ['No obvious fields were detected.'],
    })
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Upload Sample' }))
    const file = new File(['word'], 'application.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    fireEvent.change(screen.getByLabelText('Sample document'), { target: { files: [file] } })
    await screen.findByDisplayValue('Application')
    await user.click(screen.getByRole('button', { name: 'Add replacement field' }))
    const variableName = screen.getByLabelText('Automation key')
    fireEvent.change(variableName, { target: { value: 'client_name' } })
    fireEvent.change(screen.getByLabelText('Exact text in the source'), { target: { value: 'Ada Lovelace' } })
    await user.click(screen.getByRole('button', { name: 'Mark' }))
    expect(screen.getByLabelText('Extracted template body')).toHaveValue('Applicant: {{client_name}}')
    await user.click(screen.getByRole('button', { name: 'Save reusable template' }))

    await waitFor(() => expect(createTemplateFromUpload).toHaveBeenCalledTimes(1))
    const form = createTemplateFromUpload.mock.calls[0][0]
    expect(form.get('reviewed_body')).toBe('Applicant: {{client_name}}')
    expect(JSON.parse(form.get('variable_schema')).fields).toEqual([
      expect.objectContaining({ name: 'client_name', source_text: 'Ada Lovelace' }),
    ])
  })

  it('uses the binary endpoint for a side-effect-free PDF preview', async () => {
    getTemplates.mockResolvedValueOnce({ items: [{ id: 'pdf-template', title: 'Court Form', body: 'Name: {{client_name}}', category: 'other', format: 'pdf', source_filename: 'court.pdf', source_sha256: 'abc', is_active: true }] })
    const pdfBlob = new Blob(['%PDF-1.7'], { type: 'application/pdf' })
    renderTemplateFile.mockResolvedValue({ blob: pdfBlob, filename: 'Court_Form.pdf', contentType: 'application/pdf', previewId: 'preview-1', previewPurpose: 'generation' })
    URL.createObjectURL = vi.fn().mockReturnValue('blob:pdf-preview')
    URL.revokeObjectURL = vi.fn()
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Generate' }))
    await user.click(screen.getByRole('button', { name: /Smith Matter/ }))
    await user.type(screen.getByPlaceholderText('Enter Client Name'), 'Ada')
    await user.click(screen.getByRole('button', { name: 'Preview' }))

    await waitFor(() => expect(renderTemplateFile).toHaveBeenCalledWith('pdf-template', {
      variables: { client_name: 'Ada' },
      matter_id: 'matter-1',
      preview_purpose: 'generation',
    }))
    expect(renderTemplate).not.toHaveBeenCalled()
    expect(screen.getByTitle('Preview of Court Form')).toHaveAttribute('data', 'blob:pdf-preview')
    expect(screen.getByRole('button', { name: 'Download preview' })).toBeInTheDocument()
    expect(URL.createObjectURL).toHaveBeenCalledWith(pdfBlob)

    expect(screen.getByText(/These exact values and this matter are previewed/)).toBeInTheDocument()
    await user.type(screen.getByPlaceholderText('Enter Client Name'), ' Lovelace')
    await waitFor(() => expect(screen.queryByTitle('Preview of Court Form')).not.toBeInTheDocument())
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:pdf-preview')

  })

  it('downloads a source-preserving DOCX preview through the binary endpoint', async () => {
    getTemplates.mockResolvedValueOnce({ items: [{
      id: 'docx-template', title: 'Engagement Letter', body: 'Dear {{client_name}}', category: 'engagement_letter',
      format: 'docx', source_filename: 'engagement.docx', source_sha256: 'abc', is_active: true,
      variable_schema: { fields: [{ name: 'client_name', label: 'Client name', source_text: 'Ada Lovelace' }] },
    }] })
    const docxBlob = new Blob(['PK generated word'], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    renderTemplateFile.mockResolvedValue({ blob: docxBlob, filename: 'Engagement_Letter.docx', contentType: docxBlob.type })
    URL.createObjectURL = vi.fn().mockReturnValue('blob:docx-preview')
    URL.revokeObjectURL = vi.fn()
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Generate' }))
    await user.type(screen.getByRole('textbox', { name: /Client name/i }), 'Grace Hopper')
    await user.click(screen.getByRole('button', { name: 'Preview' }))

    await waitFor(() => expect(renderTemplateFile).toHaveBeenCalledWith(
      'docx-template',
      expect.objectContaining({ variables: { client_name: 'Grace Hopper' }, matter_id: null }),
    ))
    expect(await screen.findByText(/Word formatting was preserved/)).toBeInTheDocument()
    expect(screen.getByText('Engagement_Letter.docx')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:docx-preview')
  })

  it('discards and revokes a stale PDF response when values change in flight', async () => {
    getTemplates.mockResolvedValueOnce({ items: [{ id: 'race-pdf', title: 'Race Form', body: '{{client_name}}', category: 'other', format: 'pdf', source_filename: 'race.pdf', source_sha256: 'abc', is_active: true }] })
    let resolvePreview
    renderTemplateFile.mockImplementation(() => new Promise((resolve) => { resolvePreview = resolve }))
    URL.createObjectURL = vi.fn().mockReturnValue('blob:stale-preview')
    URL.revokeObjectURL = vi.fn()
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Generate' }))
    await user.click(screen.getByRole('button', { name: /Smith Matter/ }))
    const clientName = screen.getByPlaceholderText('Enter Client Name')
    await user.type(clientName, 'Ada')
    await user.click(screen.getByRole('button', { name: 'Preview' }))
    await waitFor(() => expect(renderTemplateFile).toHaveBeenCalledTimes(1))

    await user.type(clientName, ' Lovelace')
    await act(async () => {
      resolvePreview({
        blob: new Blob(['%PDF-1.7 stale'], { type: 'application/pdf' }),
        filename: 'Race_Form.pdf',
        contentType: 'application/pdf',
        previewId: 'stale-preview-id',
        previewPurpose: 'generation',
      })
      await Promise.resolve()
    })

    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:stale-preview')
    expect(screen.queryByTitle('Preview of Race Form')).not.toBeInTheDocument()
    expect(screen.queryByText(/These exact values and this matter are previewed/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Render & Save to Matter' })).toBeDisabled()
  })

  it('locks form controls and prevents modal close while a PDF save is in flight', async () => {
    getTemplates.mockResolvedValueOnce({ items: [{
      id: 'saving-pdf', title: 'Saving Form', body: '{{client_name}}', category: 'other', format: 'pdf',
      source_filename: 'saving.pdf', source_sha256: 'abc', is_active: true,
      variable_schema: { fields: [
        { name: 'client_name', label: 'Client name', pdf_field_name: 'ClientName', field_type: 'text', required: true },
      ] },
    }] })
    const pdfBlob = new Blob(['%PDF-1.7'], { type: 'application/pdf' })
    renderTemplateFile.mockResolvedValue({ blob: pdfBlob, filename: 'Saving_Form.pdf', contentType: 'application/pdf', previewId: 'saving-preview', previewPurpose: 'generation' })
    let resolveSave
    renderTemplate.mockImplementation(() => new Promise((resolve) => { resolveSave = resolve }))
    URL.createObjectURL = vi.fn().mockReturnValue('blob:saving-preview')
    URL.revokeObjectURL = vi.fn()
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Generate' }))
    await user.click(screen.getByRole('button', { name: /Smith Matter/ }))
    const clientName = screen.getByRole('textbox', { name: /Client name/ })
    await user.type(clientName, 'Ada')
    await user.click(screen.getByRole('button', { name: 'Preview' }))
    await screen.findByText(/These exact values and this matter are previewed/)
    await user.click(screen.getByRole('button', { name: 'Render & Save to Matter' }))
    await waitFor(() => expect(renderTemplate).toHaveBeenCalledTimes(1))

    expect(clientName).toBeDisabled()
    expect(screen.getByLabelText('Matter UUID fallback')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Smart Fill' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.getByRole('dialog', { name: /Generate PDF: Saving Form/ })).toBeInTheDocument()
    expect(screen.getByText(/Save in progress.*Keep this window open/)).toBeInTheDocument()

    await act(async () => {
      resolveSave({
        rendered: '',
        matter_document_id: 'saved-document',
        output_format: 'pdf',
        output_filename: 'Saving_Form-final.pdf',
      })
      await Promise.resolve()
    })
    expect(screen.getByRole('button', { name: 'Saved' })).toBeDisabled()
    expect(clientName).not.toBeDisabled()
    expect(screen.queryByText(/Save in progress.*Keep this window open/)).not.toBeInTheDocument()
  })

  it('does not overwrite manual edits with a late smart-fill response', async () => {
    let resolveSmartFill
    discoverTemplateVariables.mockImplementation(() => new Promise((resolve) => { resolveSmartFill = resolve }))
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Generate' }))
    await user.click(screen.getByRole('button', { name: /Smith Matter/ }))
    await user.click(screen.getByRole('button', { name: 'Smart Fill' }))
    await waitFor(() => expect(discoverTemplateVariables).toHaveBeenCalledTimes(1))
    const clientName = screen.getByPlaceholderText('Enter Client Name')
    await user.type(clientName, 'Manual Client')

    await act(async () => {
      resolveSmartFill({ variables: { client_name: 'Stale Suggested Client' } })
      await Promise.resolve()
    })

    expect(clientName).toHaveValue('Manual Client')
    expect(screen.getByText(/Smart-fill results were not applied because the matter or fields changed/)).toBeInTheDocument()
  })

  it('surfaces an actionable PDF render error', async () => {
    getTemplates.mockResolvedValueOnce({ items: [{ id: 'pdf-template', title: 'Flat Scan', body: '', category: 'other', format: 'pdf', source_filename: 'scan.pdf', source_sha256: 'abc', is_active: true, variable_schema: { fields: [] } }] })
    const error = new Error('This PDF has no fillable AcroForm fields.')
    error.response = { status: 422, data: { detail: error.message } }
    renderTemplateFile.mockRejectedValue(error)
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Generate' }))
    await user.click(screen.getByRole('button', { name: /Smith Matter/ }))
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
    const pdfBlob = new Blob(['%PDF-1.7'], { type: 'application/pdf' })
    renderTemplateFile.mockResolvedValue({ blob: pdfBlob, filename: 'Schema_Form.pdf', contentType: 'application/pdf', previewId: 'schema-preview-1', previewPurpose: 'generation' })
    URL.createObjectURL = vi.fn().mockReturnValue('blob:schema-preview')
    URL.revokeObjectURL = vi.fn()
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Generate' }))
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(screen.getByLabelText(/Client name/)).toHaveAttribute('id', 'template-variable-client_name')
    expect(screen.getByRole('combobox', { name: /Venue/ })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /Notes/ }).tagName).toBe('TEXTAREA')
    const signatureHeading = screen.getByText((_, element) => (
      element.tagName === 'P'
      && element.textContent.includes('Client signature')
      && element.textContent.includes('{{signature_1}}')
    ))
    expect(signatureHeading).not.toHaveAttribute('for')
    expect(signatureHeading.closest('label')).toBeNull()
    expect(screen.getByText(/Signature area is left blank for signing/)).toBeInTheDocument()
    expect(screen.getByText(/2 required fields still need review/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Smith Matter/ }))
    await user.type(screen.getByRole('textbox', { name: /Client name/ }), 'Jane')
    await user.selectOptions(screen.getByRole('combobox', { name: /Venue/ }), 'Cook County')
    expect(screen.getByText(/1 optional field left unfilled/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Render & Save to Matter' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Preview' }))
    await waitFor(() => expect(renderTemplateFile).toHaveBeenCalledWith('schema-pdf', {
      matter_id: 'matter-1',
      preview_purpose: 'generation',
      variables: { client_name: 'Jane', approved: 'false', venue: 'Cook County', notes: '' },
    }))
    await user.click(screen.getByRole('button', { name: 'Render & Save to Matter' }))

    await waitFor(() => expect(renderTemplate).toHaveBeenCalledWith('schema-pdf', {
      matter_id: 'matter-1',
      preview_id: 'schema-preview-1',
      variables: { client_name: 'Jane', approved: 'false', venue: 'Cook County', notes: '' },
    }))
  })

  it('separates partial draft diagnosis from representative activation evidence', async () => {
    getTemplates.mockResolvedValueOnce({ items: [{
      id: 'draft-pdf', title: 'Draft Court Form', body: '{{client_name}} {{notes}}', category: 'other', format: 'pdf',
      source_filename: 'draft.pdf', source_sha256: 'abc', is_active: false,
      variable_schema: { fields: [
        { name: 'client_name', label: 'Client name', pdf_field_name: 'ClientName', field_type: 'text' },
        { name: 'notes', label: 'Notes', pdf_field_name: 'Notes', field_type: 'text', multiline: true },
        { name: 'approved', label: 'Approved', pdf_field_name: 'Approved', field_type: 'checkbox' },
      ] },
    }] })
    const pdfBlob = new Blob(['%PDF-1.7'], { type: 'application/pdf' })
    renderTemplateFile
      .mockResolvedValueOnce({ blob: pdfBlob, filename: 'Draft_Court_Form.pdf', contentType: 'application/pdf', previewId: 'draft-preview-1', previewPurpose: 'draft' })
      .mockResolvedValueOnce({ blob: pdfBlob, filename: 'Draft_Court_Form.pdf', contentType: 'application/pdf', previewId: 'activation-preview-1', previewPurpose: 'activation' })
    URL.createObjectURL = vi.fn()
      .mockReturnValueOnce('blob:draft-preview')
      .mockReturnValueOnce('blob:activation-preview')
    URL.revokeObjectURL = vi.fn()
    const user = userEvent.setup()
    render(<TemplatesPage />)

    await user.click(await screen.findByRole('button', { name: 'Preview draft' }))
    const dialog = screen.getByRole('dialog', { name: /Preview Draft: Draft Court Form/ })
    await user.click(within(dialog).getByRole('button', { name: 'Preview draft' }))
    await waitFor(() => expect(renderTemplateFile).toHaveBeenNthCalledWith(1, 'draft-pdf', {
      matter_id: null,
      preview_purpose: 'draft',
      variables: { client_name: '', notes: '', approved: 'false' },
    }))
    expect(screen.getByText(/Draft preview only.*does not record activation evidence/)).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Record activation preview' }))
    expect(await screen.findByText(/Enter representative values for every non-signature PDF field/)).toBeInTheDocument()
    expect(renderTemplateFile).toHaveBeenCalledTimes(1)

    await user.type(screen.getByRole('textbox', { name: /Client name/ }), 'Representative Client')
    await user.type(screen.getByRole('textbox', { name: /Notes/ }), 'Representative narrative')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:draft-preview')
    await user.click(within(dialog).getByRole('button', { name: 'Record activation preview' }))

    await waitFor(() => expect(renderTemplateFile).toHaveBeenNthCalledWith(2, 'draft-pdf', {
      matter_id: null,
      preview_purpose: 'activation',
      variables: { client_name: 'Representative Client', notes: 'Representative narrative', approved: 'false' },
    }))
    expect(screen.getByText(/Representative activation preview recorded/)).toBeInTheDocument()
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
    expect(screen.getByText(/PDF layout and field mappings come from the retained source file/)).toBeInTheDocument()
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

  it('prevents creating a PDF analysis with no reusable source mappings', async () => {
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
    expect(await screen.findByText(/No reusable details located confidently/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save reusable template' })).toBeDisabled()
  })
})
