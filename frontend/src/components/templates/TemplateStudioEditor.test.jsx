import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import TemplateStudioEditor, { mergedVariableSchema, schemaFields } from './TemplateStudioEditor'

// pdf.js cannot rasterize in jsdom, so the shared canvas module is stubbed with
// deterministic page geometry. Everything under test here is placement state,
// not rasterization.
vi.mock('./PdfDocumentCanvas', () => ({
  PdfPageCanvas: ({ pageNumber }) => <canvas aria-label={`PDF page ${pageNumber}`} />,
  PdfThumbnail: ({ pageNumber, onSelect }) => (
    <button type="button" onClick={onSelect}>{`Show page ${pageNumber}`}</button>
  ),
  useTemplatePdfDocument: () => ({
    document: { numPages: 2 },
    pages: [
      { page: 1, width: 612, height: 792, rotation: 0 },
      { page: 2, width: 612, height: 792, rotation: 0 },
    ],
    error: '',
  }),
}))

// The binding catalogue is static server-owned vocabulary; the editor only
// needs it to populate the picker.
vi.mock('./DocxDocumentView', () => ({
  default: ({ fields, onCreateField }) => (
    <div data-testid="docx-view">
      <span>{fields.length} mapped</span>
      <button
        type="button"
        onClick={() => onCreateField({ ordinal: 2, start: 6, end: 18, text: 'Ada Lovelace' })}
      >
        Select text
      </button>
    </div>
  ),
}))

vi.mock('../../api', () => ({
  getTemplateOutline: () => Promise.resolve({ paragraphs: [], paragraph_count: 0 }),
  getTemplateBindings: () => Promise.resolve({
    bindings: [
      { path: 'client.name', label: 'Client name', group: 'Client' },
      { path: 'matter.case_number', label: 'Case number', group: 'Matter' },
    ],
    collections: [],
    operators: ['present', 'absent'],
  }),
}))

const pdfSource = () => new File(['%PDF-1.4'], 'engagement.pdf', { type: 'application/pdf' })

const templateWith = (fields, extra = {}) => ({
  id: 'a3f1c2d4-0000-4000-8000-000000000001',
  title: 'Engagement Letter',
  format: 'pdf',
  variable_schema: { version: 2, pages: [{ page: 1, width: 612, height: 792 }], fields, ...extra },
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('TemplateStudioEditor', () => {
  it('keeps server-owned schema keys when merging edited fields', () => {
    const template = templateWith([{ name: 'client_name' }], { detection: { method: 'acroform' } })
    const merged = mergedVariableSchema(template, [{ name: 'renamed' }])
    expect(merged.version).toBe(2)
    expect(merged.pages).toEqual([{ page: 1, width: 612, height: 792 }])
    expect(merged.detection).toEqual({ method: 'acroform' })
    expect(merged.fields).toEqual([{ name: 'renamed' }])
  })

  it('treats a missing or malformed field list as empty rather than throwing', () => {
    expect(schemaFields(undefined)).toEqual([])
    expect(schemaFields({ variable_schema: { fields: 'not-a-list' } })).toEqual([])
  })

  it('creates a Word field from a text selection, anchored to that span', async () => {
    // A Word field is a character span, not a rectangle, so the selection the
    // user made *is* the anchor — there is no page to place anything on.
    const onSave = vi.fn().mockResolvedValue({})
    render(
      <TemplateStudioEditor
        template={{ id: 'x', format: 'docx', variable_schema: { fields: [] } }}
        source={null}
        onSave={onSave}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Select text' }))
    await waitFor(() => expect(screen.getByText('1 mapped')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: /save fields/i }))
    await waitFor(() => expect(onSave).toHaveBeenCalled())
    const [field] = onSave.mock.calls[0][0].fields
    expect(field.docx_anchor).toEqual({ paragraph_ordinal: 2, start: 6, end: 18 })
    // The renderer re-checks this exact text before replacing it.
    expect(field.source_text).toBe('Ada Lovelace')
    expect(field.name).toBe('ada_lovelace')
  })

  it('renders the document view for a Word template', () => {
    render(
      <TemplateStudioEditor
        template={{ id: 'x', format: 'docx', variable_schema: { fields: [] } }}
        source={null}
        onSave={vi.fn()}
      />,
    )
    expect(screen.getByTestId('docx-view')).toBeInTheDocument()
  })

  it('keeps fields editable for a non-PDF template instead of dead-ending', () => {
    // Word is the format firms author in. Refusing to show its fields would
    // leave the binding and condition controls unreachable for exactly the
    // templates that most need them.
    render(
      <TemplateStudioEditor
        template={{
          id: 'x',
          format: 'docx',
          variable_schema: { fields: [{ name: 'client_name', label: 'Client' }] },
        }}
        source={null}
        onSave={vi.fn()}
      />,
    )
    expect(screen.getByRole('complementary', { name: /field properties/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Client' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /save fields/i })).toBeInTheDocument()
    expect(screen.queryByLabelText(/PDF page/i)).not.toBeInTheDocument()
  })

  it('documents the logic markers a markdown author writes in the body', () => {
    render(
      <TemplateStudioEditor
        template={{ id: 'x', format: 'markdown', variable_schema: { fields: [] } }}
        source={null}
        onSave={vi.fn()}
      />,
    )
    expect(screen.getByText('{{#if field}} … {{/if}}')).toBeInTheDocument()
    expect(screen.getByText('{{#each parties}} … {{/each}}')).toBeInTheDocument()
  })

  it('offers a data binding and a condition for the selected field', async () => {
    render(
      <TemplateStudioEditor
        template={templateWith([{ name: 'client_name' }, { name: 'is_entity' }])}
        source={pdfSource()}
        onSave={vi.fn()}
      />,
    )
    const binding = await screen.findByLabelText(/fills from/i)
    // Falls back to name matching until the customer says otherwise.
    expect(binding).toHaveValue('')
    fireEvent.change(binding, { target: { value: 'client.name' } })
    expect(binding).toHaveValue('client.name')
    expect(screen.getByText(/fills from the matter every time/i)).toBeInTheDocument()

    const condition = screen.getByLabelText(/only include when/i)
    // A field cannot be conditioned on itself.
    expect(screen.getByRole('option', { name: 'is_entity' })).toBeInTheDocument()
    fireEvent.change(condition, { target: { value: 'is_entity' } })
    expect(screen.getByLabelText('Condition')).toHaveValue('present')
  })

  it('surfaces a source load failure instead of a blank editor', () => {
    render(
      <TemplateStudioEditor
        template={templateWith([])}
        source={null}
        sourceError="The retained source could not be loaded."
        onSave={vi.fn()}
      />,
    )
    expect(screen.getByText('The retained source could not be loaded.')).toBeInTheDocument()
  })

  it('adds a manual field to the current page and marks the editor dirty', () => {
    render(<TemplateStudioEditor template={templateWith([])} source={pdfSource()} onSave={vi.fn()} />)
    expect(screen.getByRole('status')).toHaveTextContent('No changes')
    fireEvent.click(screen.getByRole('button', { name: 'Text' }))
    expect(screen.getByRole('status')).toHaveTextContent('Unsaved changes')
    expect(screen.getByDisplayValue('field_1')).toBeInTheDocument()
  })

  it('saves the merged schema and reports the save', async () => {
    const onSave = vi.fn().mockResolvedValue({})
    render(<TemplateStudioEditor template={templateWith([])} source={pdfSource()} onSave={onSave} />)
    fireEvent.click(screen.getByRole('button', { name: 'Date' }))
    fireEvent.click(screen.getByRole('button', { name: /Save fields/i }))
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
    const schema = onSave.mock.calls[0][0]
    expect(schema.version).toBe(2)
    expect(schema.fields).toHaveLength(1)
    expect(schema.fields[0].field_type).toBe('date')
    expect(schema.fields[0].pdf_overlay.page).toBe(1)
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/^Saved /))
  })

  it('reports a failed save and keeps the work in the editor', async () => {
    const onSave = vi.fn().mockRejectedValue(new Error('Template is locked'))
    render(<TemplateStudioEditor template={templateWith([])} source={pdfSource()} onSave={onSave} />)
    fireEvent.click(screen.getByRole('button', { name: 'Text' }))
    fireEvent.click(screen.getByRole('button', { name: /Save fields/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Template is locked')
    expect(screen.getByRole('status')).toHaveTextContent('Unsaved changes')
  })

  it('blocks a save that would persist a duplicate variable name', () => {
    render(
      <TemplateStudioEditor
        template={templateWith([
          { name: 'client_name', label: 'Client', pdf_source_key: 'manual:1', page: 1, pdf_overlays: [{ page: 1, rect: [10, 10, 100, 30] }] },
          { name: 'other', label: 'Other', pdf_source_key: 'manual:2', page: 1, pdf_overlays: [{ page: 1, rect: [10, 50, 100, 70] }] },
        ])}
        source={pdfSource()}
        onSave={vi.fn()}
      />,
    )
    fireEvent.change(screen.getByDisplayValue('client_name'), { target: { value: 'other' } })
    expect(screen.getByRole('alert')).toHaveTextContent(/Duplicate variable name: other/)
    expect(screen.getByRole('button', { name: /Save fields/i })).toBeDisabled()
  })

  it('blocks a save that would persist a name the renderer cannot resolve', () => {
    render(
      <TemplateStudioEditor
        template={templateWith([
          { name: 'client_name', label: 'Client', pdf_source_key: 'manual:1', page: 1, pdf_overlays: [{ page: 1, rect: [10, 10, 100, 30] }] },
        ])}
        source={pdfSource()}
        onSave={vi.fn()}
      />,
    )
    fireEvent.change(screen.getByDisplayValue('client_name'), { target: { value: '9 bad name' } })
    expect(screen.getByRole('alert')).toHaveTextContent(/Invalid variable name/)
    expect(screen.getByRole('button', { name: /Save fields/i })).toBeDisabled()
  })

  it('excludes a document-owned field but deletes a manual one', () => {
    render(
      <TemplateStudioEditor
        template={templateWith([
          { name: 'acro_field', label: 'From the PDF', pdf_field_name: 'acro_field', page: 1 },
        ])}
        source={pdfSource()}
        onSave={vi.fn()}
      />,
    )
    // An AcroForm field still exists in the document, so it is excluded.
    expect(screen.getByRole('button', { name: /Exclude field/i })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Text' }))
    expect(screen.getByRole('button', { name: /Delete field/i })).toBeInTheDocument()
  })

  it('restores the previous placement state through undo', () => {
    render(<TemplateStudioEditor template={templateWith([])} source={pdfSource()} onSave={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Text' }))
    expect(screen.getByDisplayValue('field_1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(screen.queryByDisplayValue('field_1')).not.toBeInTheDocument()
    expect(screen.getByText(/No fields yet/i)).toBeInTheDocument()
  })

  it('places a new field on the page the author is looking at', () => {
    render(<TemplateStudioEditor template={templateWith([])} source={pdfSource()} onSave={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Show page 2' }))
    fireEvent.click(screen.getByRole('button', { name: 'Text' }))
    expect(screen.getByText(/Page 2/)).toBeInTheDocument()
  })
})
