import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import WorkspaceMcpActivityPanel from './WorkspaceMcpActivityPanel'

const api = vi.hoisted(() => ({ getWorkspaceMcpActivity: vi.fn(), getWorkspaceMcpActiveGrants: vi.fn() }))
vi.mock('../api', () => api)
beforeEach(() => {
  vi.resetAllMocks()
  api.getWorkspaceMcpActiveGrants.mockResolvedValue({ items: [], next_offset: null })
  api.getWorkspaceMcpActivity.mockResolvedValue({ items: [], next_before: null })
})
afterEach(cleanup)
const show = () => render(<MemoryRouter><WorkspaceMcpActivityPanel /></MemoryRouter>)

it('shows empty firm activity without claiming any connections', async () => {
  show()
  await waitFor(() => expect(api.getWorkspaceMcpActivity).toHaveBeenCalled())
  expect(await screen.findByText('No active connections.')).toBeVisible()
  expect(screen.getByText('No activity recorded for this selection.')).toBeVisible()
})

it('shows the grant owner, reads, task and exact review evidence', async () => {
  api.getWorkspaceMcpActiveGrants.mockResolvedValue({ items: [{ id: 'grant', client_name: 'Claude', user_name: 'Attorney', scopes: ['documents:read'] }], next_offset: 50 })
  api.getWorkspaceMcpActivity.mockResolvedValue({ items: [{
    id: 'event', user_name: 'Attorney', client_id: 'Claude', tool_name: 'propose_workflow_run', outcome: 'success',
    metadata: { result_bytes: 100 }, created_at: '2026-09-08T12:00:00Z',
    run: { id: 'run', matter_id: 'matter', status: 'awaiting_review' },
    tasks: [{ id: 'task', status: 'review' }], artifacts: [{ id: 'artifact', revision_no: 2, status: 'approved' }],
    reviews: [{ id: 'review', reviewer_user_id: 'reviewer', revision_id: 'revision', decision: 'approved', content_sha256: 'content-hash', document_sha256: 'document-hash' }],
  }], next_before: 20 })
  show()
  expect(await screen.findByRole('link', { name: 'Review task' })).toHaveAttribute('href', '/tasks/task')
  expect(screen.getByRole('link', { name: 'Workflow run' })).toHaveAttribute('href', '/matters/matter?tab=workflow')
  expect(screen.getByText(/Document SHA-256 document-hash/)).toHaveTextContent('Revision revision')
  fireEvent.click(screen.getByRole('button', { name: 'View this connection’s activity' }))
  await waitFor(() => expect(api.getWorkspaceMcpActivity).toHaveBeenLastCalledWith({ grant_id: 'grant', before: undefined }))
  fireEvent.click(screen.getByRole('button', { name: 'Older activity' }))
  await waitFor(() => expect(api.getWorkspaceMcpActivity).toHaveBeenLastCalledWith({ grant_id: 'grant', before: 20 }))
  fireEvent.click(screen.getByRole('button', { name: 'Show latest firm activity' }))
  await waitFor(() => expect(api.getWorkspaceMcpActivity).toHaveBeenLastCalledWith({ grant_id: undefined, before: undefined }))
})

it.each([null, [], { items: 'invalid' }])('contains invalid activity responses: %j', async response => {
  api.getWorkspaceMcpActivity.mockResolvedValue(response)
  show()
  expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load firm assistant activity')
})

it('supports a permission error and refresh without exposing stale evidence', async () => {
  api.getWorkspaceMcpActivity.mockRejectedValue(new Error('403'))
  show()
  expect(await screen.findByRole('alert')).toHaveTextContent('permissions are required')
  api.getWorkspaceMcpActivity.mockResolvedValue({ items: [], next_before: null })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh activity' }))
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
})

it('does not let an older request overwrite a selected connection', async () => {
  let finishOlder
  api.getWorkspaceMcpActiveGrants.mockResolvedValue({ items: [{ id: 'grant', client_name: 'Claude', user_name: 'Attorney', scopes: [] }], next_offset: null })
  show()
  expect(await screen.findByText('No activity recorded for this selection.')).toBeVisible()
  api.getWorkspaceMcpActivity.mockImplementationOnce(() => new Promise(resolve => { finishOlder = resolve }))
  fireEvent.click(screen.getByRole('button', { name: 'Refresh activity' }))
  fireEvent.click(screen.getByRole('button', { name: 'View this connection’s activity' }))
  await waitFor(() => expect(api.getWorkspaceMcpActivity).toHaveBeenLastCalledWith({ before: undefined, grant_id: 'grant' }))
  await act(async () => finishOlder({ items: [{ id: 'stale', user_name: 'Wrong selection', metadata: {}, tasks: [], artifacts: [], reviews: [] }], next_before: null }))
  expect(screen.queryByText('Wrong selection')).not.toBeInTheDocument()
  expect(screen.getByText('Connection grant')).toBeVisible()
})
