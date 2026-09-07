import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MatterImportWizard, { groupFiles } from './MatterImportWizard'

const mocks = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn(), getContacts: vi.fn(), getMattersV2: vi.fn() }))
vi.mock('../api', () => ({ default: { post: mocks.post, get: mocks.get }, getContacts: mocks.getContacts, getMattersV2: mocks.getMattersV2 }))

beforeEach(() => {
  vi.clearAllMocks()
  mocks.getContacts.mockResolvedValue({ contacts: [] })
  mocks.getMattersV2.mockResolvedValue({ matters: [] })
})
afterEach(() => {
  vi.restoreAllMocks()
  cleanup()
})

describe('matter import', () => {
  it('sends ZIP previews as multipart form data', async () => {
    mocks.post.mockResolvedValue({ data: { files: [] } })
    const archive = new File(['payload'], 'cases.zip', { type: 'application/zip' })

    render(<MatterImportWizard />)
    fireEvent.change(screen.getByLabelText('Select ZIP'), { target: { files: [archive] } })

    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith(
      '/matter-imports/zip-preview',
      expect.any(FormData),
      expect.objectContaining({
        timeout: 0,
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    ))
    const [, body] = mocks.post.mock.calls[0]
    expect(body.get('file')).toBe(archive)
  })

  it('sends each selected folder file and its source path as multipart form data', async () => {
    const source = new File(['message'], 'history.eml', { type: 'message/rfc822' })
    Object.defineProperty(source, 'webkitRelativePath', { value: 'Former firm/history.eml' })
    vi.spyOn(crypto.subtle, 'digest').mockResolvedValue(new Uint8Array(32).buffer)
    mocks.post.mockImplementation(async (url, body) => {
      if (url === '/matter-imports') return { data: { id: 'run', files: body.files, status: 'review' } }
      if (url.endsWith('/approve')) return { data: { id: 'run', files: [{ path: 'Former firm/history.eml' }], status: 'uploading', results: {} } }
      if (url === '/matter-imports/run/file') return { data: { status: 'imported' } }
      throw new Error(`Unexpected request: ${url}`)
    })
    mocks.get.mockResolvedValue({ data: { id: 'run', files: [{ path: 'Former firm/history.eml' }], status: 'complete', results: { 'Former firm/history.eml': { status: 'imported' } } } })

    render(<MatterImportWizard matterId="matter-1" />)
    fireEvent.change(screen.getByLabelText('Select folder'), { target: { files: [source] } })
    await waitFor(() => expect(screen.getByRole('button', { name: /Review matter mappings/ })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: /Review matter mappings/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm mappings & import' }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Import complete'))
    const [, body, config] = mocks.post.mock.calls.find(([url]) => url === '/matter-imports/run/file')
    expect(body.get('file')).toBe(source)
    expect(body.get('path')).toBe('Former firm/history.eml')
    expect(config).toMatchObject({ headers: { 'Content-Type': 'multipart/form-data' } })
  })

  it('groups wrapped folders without changing source paths', () => {
    const files = [{ path: 'USB/Smith/mail.eml' }, { path: 'USB/Jones/case.pdf' }]
    expect(groupFiles(files, 2).map(f => f.group)).toEqual(['USB/Smith', 'USB/Jones'])
    expect(groupFiles(files, 0).map(f => f.group)).toEqual(['All selected files', 'All selected files'])
    expect(groupFiles(files, 2)[0].path).toBe(files[0].path)
  })

  it('reviews client and matter mappings before starting an import', async () => {
    const files = [{ path: 'Smith/mail.eml', size: 7, sha256: 'a'.repeat(64) }]
    mocks.post.mockImplementation(async (url, body) => {
      if (url.endsWith('zip-preview')) return { data: { files } }
      if (url === '/matter-imports') return { data: { ...body, status: 'review' } }
      if (url.endsWith('/approve')) return { data: { id: 'run', files, status: 'uploading', approval: body } }
      return { data: { id: 'run', files, status: 'complete', results: { 'Smith/mail.eml': { status: 'imported' } } } }
    })
    render(<MatterImportWizard />)
    fireEvent.change(screen.getByLabelText('Select ZIP'), { target: { files: [new File(['payload'], 'cases.zip')] } })
    await waitFor(() => expect(screen.getByRole('button', { name: /Review matter mappings/ })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: /Review matter mappings/ }))
    const first = await screen.findByLabelText('First name')
    fireEvent.change(first, { target: { value: 'Jane' } })
    fireEvent.change(screen.getByLabelText('Last name'), { target: { value: 'Smith' } })
    expect(mocks.post.mock.calls.some(([url]) => url.endsWith('/approve'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Confirm mappings & import' }))
    await screen.findByText(/Import complete/)
    const approval = mocks.post.mock.calls.find(([url]) => url.endsWith('/approve'))[1]
    expect(approval.mappings[0]).toMatchObject({ first_name: 'Jane', last_name: 'Smith', intake: 'review' })
    expect(approval.confirm).toBe(true)
  })

  it('pins existing-matter imports to the supplied matter', async () => {
    const files = [{ path: 'a.eml', size: 1, sha256: 'a'.repeat(64) }]
    mocks.post.mockImplementation(async (url, body) => ({ data: url.endsWith('zip-preview') ? { files } : { ...body, status: 'review' } }))
    render(<MatterImportWizard matterId="matter-1" />)
    fireEvent.change(screen.getByLabelText('Select ZIP'), { target: { files: [new File(['x'], 'x.zip')] } })
    await waitFor(() => expect(screen.getByRole('button', { name: /Review matter mappings/ })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: /Review matter mappings/ }))
    await screen.findByRole('button', { name: 'Confirm mappings & import' })
    expect(screen.queryByLabelText('First name')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Destination')).not.toBeInTheDocument()
    expect(mocks.getMattersV2).not.toHaveBeenCalled()
  })

  it('retains the batch id when planning fails, and surfaces the failure', async () => {
    mocks.post.mockImplementation(async (url) => {
      if (url.endsWith('zip-preview')) return { data: { files: [{ path: 'a', size: 1, sha256: 'a'.repeat(64) }] } }
      throw new Error('network')
    })
    render(<MatterImportWizard />)
    fireEvent.change(screen.getByLabelText('Select ZIP'), { target: { files: [new File(['x'], 'x.zip')] } })
    const review = screen.getByRole('button', { name: /Review matter mappings/ })
    await waitFor(() => expect(review).toBeEnabled())
    fireEvent.click(review)
    await screen.findByRole('alert')
    fireEvent.click(review)
    await waitFor(() => expect(mocks.post.mock.calls.filter(([url]) => url === '/matter-imports')).toHaveLength(2))
    const plans = mocks.post.mock.calls.filter(([url]) => url === '/matter-imports')
    expect(plans[0][1].id).toBe(plans[1][1].id)
  })

  it('resumes a saved batch and shows individual failures', async () => {
    mocks.get.mockResolvedValue({ data: { id: 'run', status: 'uploading', files: [{ path: 'a.eml' }], approval: { mappings: [], former_addresses: [] }, results: { 'a.eml': { status: 'failed', error: 'Retry storage' } } } })
    render(<MatterImportWizard />)
    fireEvent.change(screen.getByLabelText('Resume import ID'), { target: { value: 'run' } })
    fireEvent.click(screen.getByRole('button', { name: 'Resume saved import' }))
    expect(await screen.findByText('a.eml: failed — Retry storage')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('0 / 1 accounted')
  })
})
