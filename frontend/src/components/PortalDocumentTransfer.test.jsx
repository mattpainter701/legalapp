import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import PortalDocumentTransfer, { droppedFiles } from './PortalDocumentTransfer'
import { getClientPortalUploadLink, getClientPortalUploadPolicy, sendClientPortalMessage, uploadClientPortalDocument } from '../api'

vi.mock('../api', () => ({ getClientPortalUploadLink: vi.fn(), getClientPortalUploadPolicy: vi.fn(), sendClientPortalMessage: vi.fn(), uploadClientPortalDocument: vi.fn() }))
afterEach(cleanup)
beforeEach(() => {
  vi.resetAllMocks()
  getClientPortalUploadLink.mockResolvedValue({ url: null })
  getClientPortalUploadPolicy.mockResolvedValue({ max_upload_bytes: 50 * 1024 * 1024, max_files_per_batch: 10000, allowed_extensions: ['pdf', 'png', 'eml'] })
  uploadClientPortalDocument.mockResolvedValue({ id: 'saved' })
})

it('uploads every selected file, keeps source folders and retries only failures', async () => {
  const user = userEvent.setup()
  const onUploaded = vi.fn()
  uploadClientPortalDocument.mockResolvedValueOnce({}).mockRejectedValueOnce({ response: { data: { detail: 'Storage unavailable' } } })
  render(<PortalDocumentTransfer matterName="Smith" onUploaded={onUploaded} onSessionError={() => false} />)
  const first = new File(['one'], 'one.eml')
  Object.defineProperty(first, 'webkitRelativePath', { value: 'Smith/mail/one.eml' })
  const second = new File(['two'], 'scan.png')
  Object.defineProperty(second, 'webkitRelativePath', { value: 'Smith/scans/scan.png' })
  fireEvent.change(screen.getByLabelText(/Choose a folder/), { target: { files: [first, second] } })
  await user.type(screen.getByLabelText(/What are you sending/), 'Transfer')
  await user.click(screen.getByRole('button', { name: 'Send 2 files' }))
  await screen.findByText(/Storage unavailable/)
  expect(uploadClientPortalDocument).toHaveBeenNthCalledWith(1, first, 'Transfer', undefined, 'Smith/mail/one.eml')
  expect(uploadClientPortalDocument).toHaveBeenCalledTimes(2)
  await user.click(screen.getByRole('button', { name: 'Send 1 file' }))
  await screen.findByText('2 of 2 files sent')
  expect(uploadClientPortalDocument).toHaveBeenCalledTimes(3)
  expect(uploadClientPortalDocument.mock.calls[2][0]).toBe(second)
  expect(onUploaded).toHaveBeenCalledTimes(2)
})

it('stops the batch on expired portal access', async () => {
  const user = userEvent.setup()
  const onUploaded = vi.fn()
  uploadClientPortalDocument.mockRejectedValue({ response: { status: 401 } })
  render(<PortalDocumentTransfer onUploaded={onUploaded} onSessionError={() => true} />)
  fireEvent.change(screen.getByLabelText('Choose files'), { target: { files: [new File(['a'], 'a.pdf'), new File(['b'], 'b.pdf')] } })
  await user.click(screen.getByRole('button', { name: 'Send 2 files' }))
  await waitFor(() => expect(uploadClientPortalDocument).toHaveBeenCalledTimes(1))
  expect(onUploaded).not.toHaveBeenCalled()
})

it('shows only the explicitly published upload link and submits a source for review', async () => {
  const user = userEvent.setup()
  getClientPortalUploadLink.mockResolvedValue({ url: 'https://files.example/request' })
  sendClientPortalMessage.mockResolvedValue({})
  render(<PortalDocumentTransfer onUploaded={vi.fn()} onSessionError={() => false} />)
  expect(await screen.findByRole('link')).toHaveAttribute('href', 'https://files.example/request')
  await user.type(screen.getByLabelText(/Or paste a link/), 'https://files.example/source')
  await user.click(screen.getByRole('button', { name: 'Send link to my legal team' }))
  expect(sendClientPortalMessage).toHaveBeenCalledWith({ subject: 'Files to import: shared folder', body: expect.stringContaining('https://files.example/source') })
  expect(await screen.findByText(/will review the folder/)).toBeInTheDocument()
  expect(uploadClientPortalDocument).not.toHaveBeenCalled()
})

it('rejects non-HTTPS links and reports message failures', async () => {
  const user = userEvent.setup()
  render(<PortalDocumentTransfer onUploaded={vi.fn()} onSessionError={() => false} />)
  const input = screen.getByLabelText(/Or paste a link/)
  await user.type(input, 'http://files.example/source')
  await user.click(screen.getByRole('button', { name: 'Send link to my legal team' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('HTTPS')
  expect(sendClientPortalMessage).not.toHaveBeenCalled()
  await user.clear(input)
  await user.type(input, 'https://files.example/source')
  sendClientPortalMessage.mockRejectedValue(new Error('offline'))
  await user.click(screen.getByRole('button', { name: 'Send link to my legal team' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not send')
})

it('reads every directory batch and handles ordinary file drops', async () => {
  const file = new File(['body'], 'a.eml')
  const entry = { isFile: true, file: resolve => resolve(file) }
  const batches = [[entry], [entry], []]
  const directory = { name: 'Smith', isDirectory: true, createReader: () => ({ readEntries: resolve => resolve(batches.shift()) }) }
  expect(await droppedFiles({ items: [{ webkitGetAsEntry: () => directory }] })).toEqual([{ file, path: 'Smith/a.eml' }, { file, path: 'Smith/a.eml' }])
  expect(await droppedFiles({ files: [file] })).toEqual([{ file, path: 'a.eml' }])
})
