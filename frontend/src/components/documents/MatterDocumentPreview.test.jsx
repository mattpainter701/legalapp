import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import MatterDocumentPreview from './MatterDocumentPreview'
const load = vi.hoisted(() => vi.fn())
vi.mock('../../api', () => ({ getMatterDocumentSigningSource: load, getMatterDocumentDownloadUrl: () => '/download' }))
vi.mock('../templates/GeneratedPdfPreview', () => ({ default: ({ source }) => <p>Preview bytes: {source instanceof Blob ? source.size : 'invalid source'}</p> }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
it('loads authenticated file bytes before rendering a PDF', async () => {
  load.mockResolvedValue(new Blob(['%PDF-content'], { type: 'application/pdf' }))
  render(<MatterDocumentPreview matterId="jane" document={{ id: 'fee', filename: 'Fee.pdf' }} />)
  await screen.findByText('Preview bytes: 12')
  expect(load).toHaveBeenCalledWith('jane', 'fee')
})
it('retains download and a clear error when preview loading fails', async () => {
  load.mockRejectedValue(new Error('offline'))
  render(<MatterDocumentPreview matterId="jane" document={{ id: 'fee', filename: 'Fee.pdf' }} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load')
  expect(screen.getByRole('link', { name: 'Download Fee.pdf' })).toHaveAttribute('href', '/download')
})
