import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, test, expect } from 'vitest'
import api from '../api'
import ClientSignatureDocument from './ClientSignatureDocument'
vi.mock('../api', () => ({ default: { get: vi.fn() }, downloadClientPortalDocumentUrl: id => `/download/${id}` }))
vi.mock('./templates/GeneratedPdfPreview', () => ({ default: ({ source }) => <p>{source instanceof Blob ? 'PDF bytes loaded' : 'Invalid PDF source'}</p> }))
test('client must load the source before confirming review; failed load can retry', async () => {
  const onReviewed = vi.fn()
  api.get.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ data: new Blob(['%PDF-source']) })
  render(<ClientSignatureDocument request={{ document_id: 'fee', document_name: 'Fee agreement' }} reviewed={false} onReviewed={onReviewed} />)
  await screen.findByRole('alert')
  expect(screen.getByRole('checkbox')).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'Retry document' }))
  await screen.findByText('PDF bytes loaded')
  await userEvent.click(screen.getByRole('checkbox'))
  await waitFor(() => expect(onReviewed).toHaveBeenCalledWith(true))
  expect(api.get).toHaveBeenLastCalledWith('/portal/client/documents/fee/download', { responseType: 'blob' })
})
