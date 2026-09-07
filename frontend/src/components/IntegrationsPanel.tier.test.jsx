import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ProviderCard } from './IntegrationsPanel'

vi.mock('../api', () => ({
  getAdminPermissions: vi.fn(),
  triggerUserSync: vi.fn(),
  retryCloudInit: vi.fn(),
  getAdminSettings: vi.fn(),
  updateAdminSettings: vi.fn(),
  triggerCloudSync: vi.fn(),
  getIntegrationReadiness: vi.fn(),
  getSharePointBinding: vi.fn(),
  listSharePointSites: vi.fn(),
  listSharePointDrives: vi.fn(),
  saveSharePointBinding: vi.fn(),
  uploadTabs3ImportBundle: vi.fn(),
  getExternalImportTables: vi.fn(),
  reconcileExternalImport: vi.fn(),
  API_BASE_URL: '',
}))

const baseInfo = {
  connected: true,
  health: 'healthy',
  account_label: 'Personal Google (Gmail)',
  last_sync_status: 'not_applicable',
  last_sync_error: "Directory sync isn't available on personal Google accounts",
  last_sync_at: null,
  last_refresh_at: null,
  required_scopes: [],
  missing_required: [],
  granted_scopes: [],
  capabilities: {
    directory_sync: { available: false, status: 'unavailable', reason: 'personal account' },
    cloud_storage: { available: true, status: 'ok', reason: 'available' },
  },
}

const props = {
  name: 'Google Workspace',
  provider: 'google',
  scopeLabels: {},
  onReauthorize: vi.fn(),
  relTime: () => 'never',
  onSyncNow: vi.fn(),
  syncing: false,
}

describe('ProviderCard tier status', () => {
  it('shows a neutral not-applicable state for personal accounts', () => {
    render(<ProviderCard {...props} info={baseInfo} />)
    expect(screen.getByText('Personal Google (Gmail)')).toBeTruthy()
    expect(screen.getByText(/directory sync not available on this tier/)).toBeTruthy()
    expect(screen.getByText('Not on this tier')).toBeTruthy()
    expect(screen.queryByText(baseInfo.last_sync_error)).toBeNull()
  })

  it('shows genuine sync errors when status is failed', () => {
    render(<ProviderCard {...props} info={{ ...baseInfo, last_sync_status: 'failed' }} />)
    expect(screen.getByText(baseInfo.last_sync_error)).toBeTruthy()
    expect(screen.getByText(/last sync failed/)).toBeTruthy()
  })
})
