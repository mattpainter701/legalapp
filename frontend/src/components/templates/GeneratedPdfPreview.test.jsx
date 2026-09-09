import { useEffect } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import GeneratedPdfPreview from './GeneratedPdfPreview'
import { useTemplatePdfDocument } from './PdfDocumentCanvas'

vi.mock('./PdfDocumentCanvas', () => ({
  useTemplatePdfDocument: vi.fn(),
  PdfPageCanvas: function MockPage({ pageNumber, zoom, onViewport, onError }) {
    useEffect(() => { onViewport({ width: 600, height: 800 }) }, [onViewport])
    return <div><output aria-label="Rendered page">{pageNumber} at {zoom}</output><button onClick={onError}>Fail page render</button></div>
  },
}))
const source = new Blob(['output'])
beforeEach(() => {
  useTemplatePdfDocument.mockReturnValue({ document: {}, pages: [{ page: 1, width: 600, height: 800, rotation: 0 }, { page: 2, width: 600, height: 800, rotation: 90 }], error: '' })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('renders the exact output with bounded page navigation and zoom', () => {
  render(<GeneratedPdfPreview source={source} title="Matter cover" />)
  expect(useTemplatePdfDocument).toHaveBeenCalledWith(source)
  expect(screen.getByRole('region', { name: 'Preview of Matter cover' })).toBeVisible()
  expect(screen.getByText('Previous page')).toBeDisabled()
  fireEvent.click(screen.getByText('Next page'))
  expect(screen.getByLabelText('Rendered page')).toHaveTextContent('2 at 0.75')
  expect(screen.getByText('Next page')).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Preview zoom'), { target: { value: '1.25' } })
  expect(screen.getByLabelText('Rendered page')).toHaveTextContent('2 at 1.25')
  fireEvent.click(screen.getByText('Previous page'))
  expect(screen.getByLabelText('Preview page')).toHaveValue('1')
  fireEvent.change(screen.getByLabelText('Preview page'), { target: { value: '2' } })
  expect(screen.getByLabelText('Rendered page')).toHaveTextContent('2 at 1.25')
})

it('fits the canvas to its container and disconnects the resize observer', () => {
  const disconnect = vi.fn()
  vi.stubGlobal('ResizeObserver', class { constructor(callback) { this.callback = callback } observe() { this.callback([{ contentRect: { width: 332 } }]) } disconnect = disconnect })
  const view = render(<GeneratedPdfPreview source={source} title="Small screen" />)
  expect(screen.getByLabelText('Rendered page')).toHaveTextContent('1 at 0.5')
  view.unmount()
  expect(disconnect).toHaveBeenCalled()
})

it('shows a loading state instead of an empty PDF plug-in', () => {
  useTemplatePdfDocument.mockReturnValue({ document: null, pages: [], error: '' })
  render(<GeneratedPdfPreview source={source} title="Loading" />)
  expect(screen.getByRole('status')).toHaveTextContent('Opening generated PDF')
  expect(screen.getByLabelText('Preview page')).toBeDisabled()
  expect(screen.queryByLabelText('Rendered page')).not.toBeInTheDocument()
})

it('gives an actionable error when the output cannot be opened', () => {
  useTemplatePdfDocument.mockReturnValue({ document: null, pages: [], error: 'Invalid PDF' })
  render(<GeneratedPdfPreview source={source} title="Failed" />)
  expect(screen.getByRole('alert')).toHaveTextContent('Download it to inspect every page')
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it('reports page rendering failures and allows another page to be inspected', () => {
  render(<GeneratedPdfPreview source={source} title="Render failure" />)
  fireEvent.click(screen.getByText('Fail page render'))
  expect(screen.getByRole('alert')).toHaveTextContent('This page could not be displayed')
  fireEvent.click(screen.getByText('Next page'))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByLabelText('Rendered page')).toHaveTextContent('2 at')
})
