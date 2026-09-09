import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getTemplateSourcePreview } from '../../api'
import WordDocumentPreview from './WordDocumentPreview'

const pdfState = vi.hoisted(() => ({ error: '', document: { getPage: vi.fn() } }))
vi.mock('../../api', () => ({ getTemplateSourcePreview: vi.fn() }))
vi.mock('./WordPlaceholderLayer', () => ({ default: ({ pageNumber, fields, onSelectField }) => <button onClick={() => onSelectField?.(`${fields?.[0]?.name}:0`)}>Highlight page {pageNumber}</button> }))
vi.mock('./PdfDocumentCanvas', () => ({
  useTemplatePdfDocument: () => ({ document: pdfState.document, pages: [{ page: 1, width: 612, height: 792 }, { page: 2, width: 612, height: 792 }], error: pdfState.error }),
  PdfPageCanvas: ({ pageNumber, zoom, onError }) => <div data-testid="page" data-zoom={zoom}>Rendered page {pageNumber}<button onClick={onError}>Fail canvas</button></div>,
  PdfThumbnail: ({ pageNumber, onSelect }) => <button onClick={onSelect}>Thumbnail {pageNumber}</button>,
}))

const component = (id = 'one', digest = 'sha') => <WordDocumentPreview templateId={id} sourceDigest={digest}><p>Editable source fields</p></WordDocumentPreview>
beforeEach(() => { pdfState.error = ''; getTemplateSourcePreview.mockReset(); pdfState.document.getPage.mockReset() })
afterEach(cleanup)

describe('Word document preview', () => {
  it('discards a pending search when the selected field changes', async () => {
    getTemplateSourcePreview.mockResolvedValue(new Blob(['pdf']))
    let finish
    pdfState.document.getPage.mockResolvedValue({ getTextContent: () => new Promise(resolve => { finish = resolve }) })
    const fields = [{ name: 'client' }, { name: 'signature' }]
    const { rerender } = render(<WordDocumentPreview templateId="one" fields={fields} selectedIdentity="client:0" />)
    await screen.findByTestId('page')
    fireEvent.click(screen.getByRole('button', { name: 'Find selected field' }))
    await waitFor(() => expect(finish).toBeDefined())
    rerender(<WordDocumentPreview templateId="one" fields={fields} selectedIdentity="signature:1" />)
    await act(async () => finish({ items: [{ str: '{{client}}' }] }))
    expect(screen.queryByText('Field found on')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Find selected field' })).toBeEnabled()
    expect(screen.getByText('Rendered page 1')).toBeVisible()
  })
  it.each(['unique', 'repeated', 'missing', 'error'])('finds selected fields across pages with an honest %s result', async kind => {
    getTemplateSourcePreview.mockResolvedValue(new Blob(['pdf']))
    pdfState.document.getPage.mockImplementation(async number => {
      if (kind === 'error') throw new Error('private details')
      return { getTextContent: async () => ({ items: [{ str: kind === 'repeated' || (kind === 'unique' && number === 2) ? '{{client}}' : 'Other text' }] }) }
    })
    render(<WordDocumentPreview templateId="one" fields={[{ name: 'client' }]} selectedIdentity="client:0" />)
    await screen.findByTestId('page')
    fireEvent.click(screen.getByRole('button', { name: 'Find selected field' }))
    if (kind === 'unique') {
      expect(await screen.findByRole('button', { name: 'Page 2', exact: true })).toBeVisible()
      expect(screen.getByText('Rendered page 2')).toBeVisible()
      fireEvent.change(screen.getByLabelText('Go to document page'), { target: { value: '1' } })
      expect(screen.getByText('Rendered page 1')).toBeVisible()
      fireEvent.click(screen.getByRole('button', { name: 'Find selected field' }))
      await screen.findByText('Rendered page 2')
      expect(pdfState.document.getPage).toHaveBeenCalledTimes(2)
    } else if (kind === 'repeated') {
      await screen.findByText(/multiple pages/)
      expect(screen.getByText('Rendered page 1')).toBeVisible()
      fireEvent.click(screen.getByRole('button', { name: 'Page 2', exact: true }))
      expect(screen.getByText('Rendered page 2')).toBeVisible()
    } else {
      await screen.findByText(kind === 'error' ? /Could not search/ : /No verified location found/)
      expect(screen.queryByText(/private details/)).not.toBeInTheDocument()
      expect(screen.getByText('Rendered page 1')).toBeVisible()
    }
  })
  it.each(['loading', 'failed'])('offers usable text selection when the document is %s', async state => {
    getTemplateSourcePreview.mockImplementation(() => state === 'failed'
      ? Promise.reject(new Error('converter unavailable'))
      : new Promise(() => {}))
    render(<WordDocumentPreview templateId="one" onCreateField={vi.fn()}><p>Text tools</p></WordDocumentPreview>)
    if (state === 'failed') await screen.findByText(/Document preview is unavailable/)
    fireEvent.click(screen.getByRole('button', { name: 'Add field', exact: true }))
    expect(screen.getByRole('button', { name: 'Fields', exact: true })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('Text tools')).toBeVisible()
    expect(screen.getByText(/Select the words to replace in the text below/)).toBeVisible()
    expect(screen.queryByText(/A field name box will open/)).not.toBeInTheDocument()
  })
  it('keeps Add field on the document and explains direct selection', async () => {
    getTemplateSourcePreview.mockResolvedValue(new Blob(['pdf']))
    render(<WordDocumentPreview templateId="one" onCreateField={vi.fn()}><p>Text tools</p></WordDocumentPreview>)
    await screen.findByTestId('page')
    fireEvent.click(screen.getByRole('button', { name: 'Fields', exact: true }))
    fireEvent.click(screen.getByRole('button', { name: 'Add field', exact: true }))
    expect(screen.getByTestId('page')).toBeVisible()
    expect(screen.getByText('Text tools')).not.toBeVisible()
    expect(screen.getByText(/A field name box will open/)).toBeVisible()
  })
  it('connects page highlights to field selection and remounts them after the Fields view', async () => {
    getTemplateSourcePreview.mockResolvedValue(new Blob(['pdf']))
    const select = vi.fn()
    render(<WordDocumentPreview templateId="one" fields={[{ name: 'client_name' }]} onSelectField={select}><p>Text mapping</p></WordDocumentPreview>)
    fireEvent.click(await screen.findByRole('button', { name: 'Highlight page 1' }))
    expect(select).toHaveBeenCalledWith('client_name:0')
    fireEvent.click(screen.getByRole('button', { name: 'Fields', exact: true }))
    expect(screen.queryByRole('button', { name: 'Highlight page 1', hidden: true })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Document', exact: true }))
    expect(screen.getByRole('button', { name: 'Highlight page 1' })).toBeVisible()
  })
  it('shows source pages by default, navigates and zooms, and retains the field view', async () => {
    getTemplateSourcePreview.mockResolvedValue(new Blob(['pdf']))
    render(component())
    expect(await screen.findByText('Rendered page 1')).toBeVisible()
    expect(screen.getByText('Editable source fields')).not.toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(screen.getByText('Rendered page 2')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Previous page' }))
    fireEvent.click(screen.getByRole('button', { name: 'Pages', exact: true }))
    fireEvent.click(screen.getByRole('button', { name: 'Thumbnail 2' }))
    expect(screen.getByText('Rendered page 2')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in document' }))
    expect(Number(screen.getByTestId('page').dataset.zoom)).toBeGreaterThan(0.9)
    fireEvent.click(screen.getByRole('button', { name: 'Zoom out document' }))
    fireEvent.click(screen.getByRole('button', { name: 'Fit document width' }))
    fireEvent.click(screen.getByRole('button', { name: 'Fields', exact: true }))
    expect(screen.getByText('Editable source fields')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Document', exact: true }))
    expect(screen.getByTestId('page')).toBeVisible()
    expect(getTemplateSourcePreview).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByText('Document help'))
    expect(screen.getByText(/Filled values can change pagination/)).toBeVisible()
  })

  it('keeps fields available during conversion and after failure, and allows retry', async () => {
    getTemplateSourcePreview.mockRejectedValueOnce(new Error('private converter path')).mockResolvedValueOnce(new Blob(['pdf']))
    render(component())
    expect(screen.getByText('Editable source fields')).not.toBeVisible()
    expect(await screen.findByText(/Document preview is unavailable/)).toBeVisible()
    expect(screen.queryByText(/private converter path/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry document preview' }))
    expect(await screen.findByTestId('page')).toBeVisible()
  })

  it.each(['parser', 'canvas'])('falls back when the PDF %s fails', async kind => {
    if (kind === 'parser') pdfState.error = 'PDF load failed'
    getTemplateSourcePreview.mockResolvedValue(new Blob(['pdf']))
    render(component())
    if (kind === 'canvas') fireEvent.click(await screen.findByRole('button', { name: 'Fail canvas' }))
    expect(await screen.findByText(/Document preview is unavailable/)).toBeVisible()
    expect(screen.getByText('Editable source fields')).toBeVisible()
  })

  it.each([[403, /do not have access/], [409, /could not be verified/]])('distinguishes source access failures (%s) from converter availability', async (status, message) => {
    getTemplateSourcePreview.mockRejectedValue({ response: { status } })
    render(component())
    expect(await screen.findByText(message)).toBeVisible()
    expect(screen.queryByTestId('page')).not.toBeInTheDocument()
  })

  it('ignores a stale request after switching templates and invalidates a changed source', async () => {
    let resolveOld
    getTemplateSourcePreview.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve }))
      .mockResolvedValue(new Blob(['new pdf']))
    const { rerender } = render(component())
    rerender(component('two'))
    await screen.findByTestId('page')
    await act(async () => { resolveOld(new Blob(['old pdf'])) })
    expect(getTemplateSourcePreview.mock.calls.map(call => call[0])).toEqual(['one', 'two'])
    rerender(component('two', 'changed'))
    await waitFor(() => expect(getTemplateSourcePreview).toHaveBeenCalledTimes(3))
    expect(await screen.findByTestId('page')).toBeVisible()
  })

  it('does not switch away from fields if the author chose them while loading', async () => {
    let finish
    getTemplateSourcePreview.mockReturnValue(new Promise(resolve => { finish = resolve }))
    render(component())
    fireEvent.click(screen.getByRole('button', { name: 'Fields', exact: true }))
    await act(async () => { finish(new Blob(['pdf'])) })
    expect(screen.getByText('Editable source fields')).toBeVisible()
    expect(screen.getByTestId('page')).not.toBeVisible()
  })

  it('keeps the text view when the author starts mapping during conversion', async () => {
    let finish
    getTemplateSourcePreview.mockReturnValue(new Promise(resolve => { finish = resolve }))
    render(component())
    fireEvent.click(screen.getByRole('button', { name: 'Add field from text' }))
    fireEvent.pointerDown(screen.getByText('Editable source fields'))
    await act(async () => { finish(new Blob(['pdf'])) })
    expect(screen.getByText('Editable source fields')).toBeVisible()
    expect(screen.getByTestId('page')).not.toBeVisible()
  })

  it('renders an upload without a saved template and ignores stale file responses', async () => {
    let finishOld
    const load = vi.fn().mockReturnValueOnce(new Promise(resolve => { finishOld = resolve })).mockResolvedValue(new Blob(['new pdf']))
    const first = new File(['old'], 'sample.docx')
    const second = new File(['new'], 'sample.docx')
    const { rerender } = render(<WordDocumentPreview file={first} loadUploadPreview={load}><p>Upload text</p></WordDocumentPreview>)
    expect(screen.getByText('Upload text')).not.toBeVisible()
    rerender(<WordDocumentPreview file={second} loadUploadPreview={load}><p>Upload text</p></WordDocumentPreview>)
    await screen.findByTestId('page')
    await act(async () => { finishOld(new Blob(['old pdf'])) })
    expect(load.mock.calls.map(call => call[0])).toEqual([first, second])
    expect(getTemplateSourcePreview).not.toHaveBeenCalled()
    expect(screen.getByTestId('page')).toBeVisible()
  })
})
