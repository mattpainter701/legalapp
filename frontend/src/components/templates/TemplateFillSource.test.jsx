import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { getTemplateOutline, getTemplateSource, getTemplateSourcePreview } from '../../api'
import TemplateFillSource from './TemplateFillSource'

vi.mock('../../api', () => ({ getTemplateOutline: vi.fn(), getTemplateSource: vi.fn(), getTemplateSourcePreview: vi.fn() }))
vi.mock('./PdfDocumentCanvas', () => ({
  useTemplatePdfDocument: source => ({ document: source ? {} : null, pages: source ? [{ page: 1, width: 612, height: 792 }, { page: 2, width: 612, height: 792 }] : [], error: '' }),
  PdfPageCanvas: ({ pageNumber }) => <div>Source page {pageNumber}</div>,
}))
vi.mock('./WordPlaceholderLayer', () => ({ default: ({ fields, onSelectField }) => <button onClick={() => onSelectField(`${fields[0].name}:0`)}>Word field</button> }))
beforeEach(() => { vi.clearAllMocks(); getTemplateOutline.mockResolvedValue({ paragraphs: [] }) })
afterEach(cleanup)
const fields = [{ name: 'client', label: 'Client name' }]

it('shows entered values in a text template and focuses the corresponding field', () => {
  const select = vi.fn()
  render(<TemplateFillSource template={{ id: 'one', format: 'markdown', body: 'Dear {{client}}' }} fields={fields} values={{ client: 'Taylor' }} onSelectField={select} />)
  fireEvent.click(screen.getByRole('button', { name: 'Fill Client name' }))
  expect(select).toHaveBeenCalledWith('client')
  expect(screen.getByText('Taylor')).toBeVisible()
  expect(getTemplateSource).not.toHaveBeenCalled()
})

it('never presents a newer draft as the published source', () => {
  render(<TemplateFillSource template={{ id: 'one', format: 'docx', is_active: true, published_version_no: 2, current_version_no: 3 }} fields={fields} values={{}} />)
  expect(screen.getByText(/newer draft edits/)).toBeVisible()
  expect(getTemplateSourcePreview).not.toHaveBeenCalled()
})

it('loads Word page references and links detected fields to the value form', async () => {
  getTemplateSourcePreview.mockResolvedValue(new Blob(['pdf']))
  const select = vi.fn()
  render(<TemplateFillSource template={{ id: 'one', format: 'docx' }} fields={fields} values={{}} onSelectField={select} />)
  await waitFor(() => expect(screen.getByText('Page 1 of 2')).toBeVisible())
  fireEvent.click(screen.getByText('Word field'))
  expect(select).toHaveBeenCalledWith('client')
  fireEvent.click(screen.getByText('Next source page'))
  expect(screen.getByText('Source page 2')).toBeVisible()
  fireEvent.click(screen.getByText('Previous source page'))
  expect(screen.getByText('Source page 1')).toBeVisible()
})

it('keeps filling available when the source reference fails', async () => {
  getTemplateSource.mockRejectedValue(new Error('unavailable'))
  render(<TemplateFillSource template={{ id: 'one', format: 'pdf' }} fields={fields} values={{}} />)
  expect(await screen.findByText(/source reference could not be displayed/)).toBeVisible()
})

it('links PDF placements and clears old source pages when switching templates', async () => {
  getTemplateSource.mockResolvedValue(new Blob(['pdf']))
  const select = vi.fn()
  const pdfFields = [{ ...fields[0], pdf_overlay: { page: 1, x: 10, y: 10, width: 100, height: 20 } }]
  const view = render(<TemplateFillSource template={{ id: 'one', format: 'pdf' }} fields={pdfFields} values={{}} onSelectField={select} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Fill Client name' }))
  expect(select).toHaveBeenCalledWith('client')
  fireEvent.click(screen.getByText('Next source page'))
  let finish
  getTemplateSource.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  view.rerender(<TemplateFillSource template={{ id: 'two', format: 'pdf' }} fields={pdfFields} values={{}} onSelectField={select} />)
  expect(screen.getByText('Loading source document…')).toBeVisible()
  expect(screen.getByText('Source page 1')).toBeVisible()
  view.unmount()
  await act(async () => finish(new Blob(['new pdf'])))
})
