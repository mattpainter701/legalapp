import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import StorageMigrationPanel from './StorageMigrationPanel'
import * as api from '../api'

vi.mock('../api', async () => {
  const actual = await vi.importActual('../api')
  return { ...actual, startStorageMigration: vi.fn(), getLatestStorageMigration: vi.fn().mockResolvedValue(null), getStorageMigration: vi.fn(), reconcileStorageMigration: vi.fn(), getStorageMigrationMatches: vi.fn(), cutoverStorageMigration: vi.fn(), abandonStorageMigration: vi.fn() }
})

const permissions = { google: { connected: true }, microsoft: { connected: true } }

describe('StorageMigrationPanel', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('starts against a connected target and reconciles discovery results', async () => {
    api.startStorageMigration.mockResolvedValue({ id: 'm1', phase: 'planning', bucket_counts: { matched: 0, missing: 0, ambiguous: 0 } })
    api.reconcileStorageMigration.mockResolvedValue({ id: 'm1', phase: 'awaiting_confirmation', evidence_version: 'server-review-7', bucket_counts: { matched: 3, missing: 1, ambiguous: 0 } })
    api.getStorageMigrationMatches.mockResolvedValue([{ object_type: 'matter', object_id: 'matter-1', bucket: 'missing', matching_rung: null }])
    render(<StorageMigrationPanel primaryProvider="google_drive" permissions={permissions} />)
    await waitFor(() => expect(screen.getByLabelText('Target connected root')).not.toBeDisabled())
    fireEvent.change(screen.getByLabelText('Target connected root'), { target: { value: 'onedrive' } })
    fireEvent.click(screen.getByRole('button', { name: 'Start migration' }))
    await waitFor(() => expect(api.startStorageMigration).toHaveBeenCalledWith({ target_provider: 'onedrive', target_root_id: undefined, target_drive_id: undefined }))
    fireEvent.click(screen.getByRole('button', { name: 'Reconcile connected root' }))
    await waitFor(() => expect(api.getStorageMigrationMatches).toHaveBeenCalledWith('m1'))
    expect(await screen.findByText(/matter-1/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm cutover' })).toBeDisabled()
  })

  it('requires clean evidence and explicit acknowledgement before cutover', async () => {
    api.startStorageMigration.mockResolvedValue({ id: 'm2', phase: 'planning', bucket_counts: {} })
    api.reconcileStorageMigration.mockResolvedValue({ id: 'm2', phase: 'awaiting_confirmation', evidence_version: 'server-review-8', bucket_counts: { matched: 2, missing: 0, ambiguous: 0 } })
    api.getStorageMigrationMatches.mockResolvedValue([])
    api.cutoverStorageMigration.mockResolvedValue({ id: 'm2', phase: 'complete', needs_reindex: true })
    render(<StorageMigrationPanel primaryProvider="google_drive" permissions={permissions} />)
    await waitFor(() => expect(screen.getByLabelText('Target connected root')).not.toBeDisabled())
    fireEvent.change(screen.getByLabelText('Target connected root'), { target: { value: 'onedrive' } })
    fireEvent.click(screen.getByRole('button', { name: 'Start migration' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reconcile connected root' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Reconcile connected root' }))
    const confirm = await screen.findByRole('checkbox')
    fireEvent.click(confirm)
    const cutover = screen.getByRole('button', { name: 'Confirm cutover' })
    expect(cutover).not.toBeDisabled()
    fireEvent.click(cutover)
    await waitFor(() => expect(api.cutoverStorageMigration).toHaveBeenCalledWith('m2', { evidence_version: 'server-review-8', acknowledged_policy: 'all-matters-and-documents-resolved' }))
    expect(await screen.findByText(/Reindex is pending/)).toBeInTheDocument()
  })

  it('restores a durable migration on refresh and reports stale evidence', async () => {
    api.getLatestStorageMigration.mockResolvedValue({ id: 'durable-1', phase: 'awaiting_confirmation', evidence_version: 'server-1', bucket_counts: { matched: 1, missing: 0, ambiguous: 0 }, needs_reindex: false })
    api.getStorageMigrationMatches.mockResolvedValue([])
    api.cutoverStorageMigration.mockRejectedValue({ response: { data: { detail: 'Migration evidence changed; reconcile again' } } })
    render(<StorageMigrationPanel primaryProvider="google_drive" permissions={permissions} />)
    expect(await screen.findByText('awaiting_confirmation')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Refresh status' }))
    await waitFor(() => expect(api.getLatestStorageMigration).toHaveBeenCalledTimes(2))
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm cutover' }))
    expect(await screen.findByText('Migration evidence changed; reconcile again')).toBeInTheDocument()
  })

  it('submits bound SharePoint root and drive IDs by default', async () => {
    api.getLatestStorageMigration.mockResolvedValue(null)
    api.startStorageMigration.mockResolvedValue({ id: 'sp-1', phase: 'planning', bucket_counts: {} })
    render(<StorageMigrationPanel primaryProvider="google_drive" permissions={permissions} sharePointBinding={{ root_item_id: 'bound-root', drive_id: 'bound-drive' }} />)
    await waitFor(() => expect(screen.getByLabelText('Target connected root')).not.toBeDisabled())
    fireEvent.change(screen.getByLabelText('Target connected root'), { target: { value: 'sharepoint' } })
    fireEvent.click(screen.getByRole('button', { name: 'Start migration' }))
    await waitFor(() => expect(api.startStorageMigration).toHaveBeenCalledWith({ target_provider: 'sharepoint', target_root_id: 'bound-root', target_drive_id: 'bound-drive' }))
  })
})
