import React, { useEffect, useState } from 'react'
import {
  API_BASE_URL,
  getAdminPermissions,
  triggerUserSync,
  retryCloudInit,
  getAdminSettings,
  updateAdminSettings,
  triggerCloudSync,
  getIntegrationReadiness,
  getSharePointBinding,
  listSharePointSites,
  listSharePointDrives,
  saveSharePointBinding,
  uploadTabs3ImportBundle,
  getExternalImportTables,
  reconcileExternalImport,
} from '../api'

const SCOPE_LABELS_MS = {
  offline_access: 'Offline access (refresh tokens)',
  'User.Read.All': 'Read all user profiles',
  'Mail.Read': 'Read signed-in mailbox',
  'Files.Read.All': 'Read all files (OneDrive + SharePoint)',
  'Files.ReadWrite.All': 'Read & write files (OneDrive + SharePoint)',
  'Sites.Read.All': 'Access SharePoint sites',
  'Calendars.ReadWrite': 'Read and write calendars',
  openid: 'OpenID Connect',
  email: 'Email address',
  profile: 'Profile info',
}

const SCOPE_LABELS_GOOGLE = {
  'openid': 'OpenID Connect',
  'email': 'Email address',
  'profile': 'Profile info',
  'https://www.googleapis.com/auth/userinfo.email': 'Email address',
  'https://www.googleapis.com/auth/userinfo.profile': 'Profile info',
  'https://www.googleapis.com/auth/admin.directory.user.readonly': 'Read directory users',
  'https://www.googleapis.com/auth/gmail.readonly': 'Read Gmail messages',
  'https://www.googleapis.com/auth/drive.readonly': 'Read Google Drive files (read-only)',
  'https://www.googleapis.com/auth/drive': 'Read & write Google Drive (folders + files)',
  'https://www.googleapis.com/auth/calendar': 'Read & write Google Calendar',
}

const CORE_READINESS_ENV_KEYS = new Set([
  'FRONTEND_URL',
  'BACKEND_URL',
  'MICROSOFT_CLIENT_ID',
  'MICROSOFT_CLIENT_SECRET',
  'MICROSOFT_TENANT_ID',
  'GOOGLE_CLIENT_ID',
  'GOOGLE_CLIENT_SECRET',
  'TEAMS_APP_ID',
])

function Tabs3ImportPanel() {
  const [file, setFile] = useState(null)
  const [passphrase, setPassphrase] = useState('')
  const [accountingMode, setAccountingMode] = useState('tabs3_reference')
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState(null)
  const [run, setRun] = useState(null)
  const [tables, setTables] = useState([])
  const [reconcile, setReconcile] = useState(null)

  const handleUpload = async (event) => {
    event.preventDefault()
    if (!file) {
      setError('Choose a Tabs3 export bundle first.')
      return
    }
    setUploading(true)
    setError(null)
    setRun(null)
    setTables([])
    setReconcile(null)
    try {
      const uploaded = await uploadTabs3ImportBundle({ file, passphrase, accountingMode })
      setRun(uploaded)
      const [tableData, reconcileData] = await Promise.all([
        getExternalImportTables(uploaded.id),
        reconcileExternalImport(uploaded.id),
      ])
      setTables(tableData.tables || [])
      setReconcile(reconcileData)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Tabs3 import upload failed.')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-6">
      <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
        <div>
          <h3 className="text-brand-ink font-sans text-base font-bold">Tabs3 Import</h3>
          <p className="text-brand-ink-2 font-sans text-xs mt-1">
            Stage an on-prem Tabs3 export bundle for review before cutover.
          </p>
        </div>
        {run && (
          <span className="px-2.5 py-1 rounded-lg bg-green-100 text-green-700 border border-green-200 text-xs font-bold">
            {run.status}
          </span>
        )}
      </div>

      <form onSubmit={handleUpload} className="grid grid-cols-1 lg:grid-cols-[1.4fr_1fr_1fr_auto] gap-3 items-end">
        <label className="block">
          <span className="block text-xs font-bold text-brand-ink mb-1">Export bundle</span>
          <input
            type="file"
            accept=".zip,.tabs3bundle,application/zip,application/octet-stream"
            onChange={(event) => setFile(event.target.files?.[0] || null)}
            className="block w-full text-sm text-brand-ink file:mr-3 file:px-3 file:py-2 file:rounded-lg file:border file:border-brand-line file:bg-brand-bg file:text-brand-ink file:text-xs file:font-bold"
          />
        </label>
        <label className="block">
          <span className="block text-xs font-bold text-brand-ink mb-1">Passphrase</span>
          <input
            type="password"
            value={passphrase}
            onChange={(event) => setPassphrase(event.target.value)}
            placeholder="Encrypted bundles"
            className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-brand-ink font-sans text-sm focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
          />
        </label>
        <label className="block">
          <span className="block text-xs font-bold text-brand-ink mb-1">Accounting mode</span>
          <select
            value={accountingMode}
            onChange={(event) => setAccountingMode(event.target.value)}
            className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-brand-ink font-sans text-sm focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
          >
            <option value="tabs3_reference">Tabs3 reference</option>
            <option value="clarity_native">Clarity native</option>
            <option value="qbo">QuickBooks Online</option>
          </select>
        </label>
        <button
          type="submit"
          disabled={uploading}
          className="px-4 py-2 bg-brand-ink text-white rounded-lg font-sans text-sm font-bold hover:bg-brand-ink/90 disabled:opacity-50"
        >
          {uploading ? 'Uploading...' : 'Upload'}
        </button>
      </form>

      {error && (
        <div className="mt-4 px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-red-700 text-xs font-medium">
          {error}
        </div>
      )}

      {reconcile && (
        <div className="mt-5 grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="bg-brand-bg border border-brand-line rounded-lg p-3">
            <div className="text-[11px] uppercase font-bold text-brand-ink-2">Run</div>
            <div className="text-sm font-bold text-brand-ink truncate">{reconcile.export_id || run?.id}</div>
          </div>
          <div className="bg-brand-bg border border-brand-line rounded-lg p-3">
            <div className="text-[11px] uppercase font-bold text-brand-ink-2">Tables</div>
            <div className="text-sm font-bold text-brand-ink">{reconcile.table_count}</div>
          </div>
          <div className="bg-brand-bg border border-brand-line rounded-lg p-3">
            <div className="text-[11px] uppercase font-bold text-brand-ink-2">Rows</div>
            <div className="text-sm font-bold text-brand-ink">{reconcile.total_rows}</div>
          </div>
          <div className="bg-brand-bg border border-brand-line rounded-lg p-3">
            <div className="text-[11px] uppercase font-bold text-brand-ink-2">Warnings</div>
            <div className="text-sm font-bold text-brand-ink">{reconcile.warnings?.length || 0}</div>
          </div>
        </div>
      )}

      {tables.length > 0 && (
        <div className="mt-5 overflow-x-auto border border-brand-line rounded-lg">
          <table className="min-w-full text-sm">
            <thead className="bg-brand-bg-soft text-brand-ink-2 text-xs uppercase">
              <tr>
                <th className="text-left px-3 py-2 font-bold">Table</th>
                <th className="text-right px-3 py-2 font-bold">Rows</th>
                <th className="text-left px-3 py-2 font-bold">Checksum</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-brand-line">
              {tables.map((table) => (
                <tr key={table.source_table}>
                  <td className="px-3 py-2 font-mono text-xs text-brand-ink">{table.source_table}</td>
                  <td className="px-3 py-2 text-right text-brand-ink">{table.row_count}</td>
                  <td className="px-3 py-2 font-mono text-[11px] text-brand-ink-2 truncate max-w-sm">{table.checksum || 'metadata-only'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function IntegrationsPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [retryResult, setRetryResult] = useState(null)
  const [primaryCloud, setPrimaryCloud] = useState(null)
  const [cloudSaving, setCloudSaving] = useState(false)
  const [cloudSaved, setCloudSaved] = useState(false)
  const [contentSyncing, setContentSyncing] = useState(false)
  const [contentSyncResult, setContentSyncResult] = useState(null)
  const [readiness, setReadiness] = useState(null)
  const [sharePointBinding, setSharePointBinding] = useState(null)
  const [sharePointFlash, setSharePointFlash] = useState(null)

  const relTime = (iso) => {
    if (!iso) return 'never'
    const diffMs = Date.now() - new Date(iso).getTime()
    const mins = Math.floor(diffMs / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    return `${Math.floor(hrs / 24)}d ago`
  }

  const handleRetryCloudInit = async () => {
    setRetrying(true)
    setRetryResult(null)
    try {
      const result = await retryCloudInit()
      setRetryResult(result)
    } catch {
      setRetryResult({ error: 'Cloud setup failed. Check that Google or Microsoft is connected.' })
    } finally {
      setRetrying(false)
    }
  }

  const handleSyncNow = async () => {
    setSyncing(true)
    try {
      await triggerUserSync()
      setTimeout(() => {
        getAdminPermissions().then(setData).catch(() => {})
        setSyncing(false)
      }, 4000)
    } catch {
      setError('Failed to trigger sync.')
      setSyncing(false)
    }
  }

  const handlePrimaryCloudChange = async (value) => {
    const previous = primaryCloud
    const next = value === '' ? null : value
    setPrimaryCloud(next)
    setCloudSaving(true)
    setCloudSaved(false)
    try {
      await updateAdminSettings({ primary_cloud_provider: next })
      setCloudSaved(true)
    } catch {
      setPrimaryCloud(previous)
      setError('Failed to save primary cloud provider.')
    } finally {
      setCloudSaving(false)
    }
  }

  useEffect(() => {
    Promise.all([
      getAdminPermissions(),
      getAdminSettings(),
      getIntegrationReadiness().catch(() => null),
      getSharePointBinding().catch(() => ({ binding: null })),
    ])
      .then(([perms, settings, readinessData, bindingData]) => {
        setData(perms)
        setPrimaryCloud(settings.primary_cloud_provider ?? null)
        setReadiness(readinessData)
        setSharePointBinding(bindingData?.binding || null)
      })
      .catch(() => setError('Failed to load permissions.'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <div className="w-8 h-8 border-4 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!data) return null

  const handleReauthorize = (provider) => {
    const intent = 'admin'
    if (provider === 'microsoft') {
      window.location.href = `${API_BASE_URL}/integrations/microsoft/connect?intent=${intent}`
    } else {
      window.location.href = `${API_BASE_URL}/integrations/google/connect?intent=${intent}`
    }
  }

  const handleContentSync = async () => {
    setContentSyncing(true)
    setContentSyncResult(null)
    try {
      const result = await triggerCloudSync()
      setContentSyncResult(result)
    } catch {
      setContentSyncResult({ error: 'Cloud file/email sync failed.' })
    } finally {
      setContentSyncing(false)
    }
  }

  const overallColors = {
    healthy: 'bg-green-100 text-green-700 border-green-200',
    attention_needed: 'bg-amber-100 text-amber-700 border-amber-200',
    disconnected: 'bg-red-100 text-red-700 border-red-200',
  }

  const healthLabels = {
    healthy: 'All good',
    missing_scopes: 'Missing scopes',
    refresh_failed: 'Refresh failed',
    revoked: 'Reconnect required',
    disconnected: 'Not connected',
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-xs font-medium">{error}</div>
      )}

      {/* Overall status */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl border text-sm font-bold ${overallColors[data.overall_health] || overallColors.disconnected}`}>
          <span className={`w-2 h-2 rounded-full ${
            data.overall_health === 'healthy' ? 'bg-green-500' :
            data.overall_health === 'attention_needed' ? 'bg-amber-500' : 'bg-red-500'
          }`} />
          Integrations: {data.overall_health === 'healthy' ? 'Healthy' :
            data.overall_health === 'attention_needed' ? 'Needs Attention' : 'No integrations connected'}
        </div>

        {/* Cloud folder setup */}
        <div className="flex items-center gap-3 flex-wrap justify-end">
          {contentSyncResult && !contentSyncResult.error && (
            <span className="text-xs text-green-700 font-medium">
              Synced {contentSyncResult.total ?? 0} cloud item{contentSyncResult.total === 1 ? '' : 's'}
            </span>
          )}
          {contentSyncResult?.error && (
            <span className="text-xs text-red-600 font-medium">{contentSyncResult.error}</span>
          )}
          <button
            onClick={handleContentSync}
            disabled={contentSyncing}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-brand-line text-brand-ink font-sans text-xs font-medium rounded-lg hover:bg-brand-bg-soft transition-colors disabled:opacity-50"
          >
            {contentSyncing ? (
              <>
                <span className="w-3 h-3 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
                Syncing…
              </>
            ) : (
              <>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M21 12a9 9 0 11-2.64-6.36M21 3v6h-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
                Sync files + email
              </>
            )}
          </button>
          {retryResult && !retryResult.error && (
            <span className="text-xs text-green-700 font-medium">
              Cloud folders ready · {retryResult.matters_initialized} matter{retryResult.matters_initialized !== 1 ? 's' : ''} set up
            </span>
          )}
          {retryResult?.error && (
            <span className="text-xs text-red-600 font-medium">{retryResult.error}</span>
          )}
          <button
            onClick={handleRetryCloudInit}
            disabled={retrying}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-brand-line text-brand-ink font-sans text-xs font-medium rounded-lg hover:bg-brand-bg-soft transition-colors disabled:opacity-50"
          >
            {retrying ? (
              <>
                <span className="w-3 h-3 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
                Setting up…
              </>
            ) : (
              <>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M4 12a8 8 0 018-8V2L14 4l-2 2V4a6 6 0 100 12h.01" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
                Retry cloud setup
              </>
            )}
          </button>
        </div>
      </div>

      <Tabs3ImportPanel />

      {/* Primary cloud storage selector */}
      <div className="bg-brand-surface border border-brand-line rounded-xl p-6">
        <h3 className="text-brand-ink font-sans text-base font-bold mb-1">Cloud Document Storage</h3>
        <p className="text-brand-ink-2 font-sans text-xs mb-4">
          Choose which connected cloud provider stores matter documents and folders. "Auto" uses the first available provider.
        </p>
        <div className="flex items-center gap-3">
          <select
            value={primaryCloud ?? ''}
            onChange={(e) => handlePrimaryCloudChange(e.target.value)}
            disabled={cloudSaving}
            className="flex-1 max-w-xs px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-brand-ink font-sans text-sm disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
          >
            <option value="">Auto (first available)</option>
            <option value="onedrive">Microsoft OneDrive</option>
            <option value="sharepoint">Microsoft SharePoint</option>
            <option value="google_drive">Google Drive</option>
          </select>
          {cloudSaving && (
            <span className="w-4 h-4 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
          )}
          {!cloudSaving && cloudSaved && (
            <span className="text-xs text-green-700 font-medium">Saved</span>
          )}
        </div>
      </div>

      <ReadinessCard readiness={readiness} />

      <SharePointBindingCard
        binding={sharePointBinding}
        onSaved={(binding) => {
          setSharePointBinding(binding)
          setPrimaryCloud(binding?.is_primary ? 'sharepoint' : primaryCloud)
          setSharePointFlash('SharePoint binding saved.')
        }}
        flash={sharePointFlash}
        onFlashClear={() => setSharePointFlash(null)}
      />

      {/* Microsoft card */}
      <ProviderCard
        name="Microsoft 365"
        provider="microsoft"
        info={data.microsoft}
        scopeLabels={SCOPE_LABELS_MS}
        onReauthorize={handleReauthorize}
        relTime={relTime}
        onSyncNow={handleSyncNow}
        syncing={syncing}
      />

      {/* Google card */}
      <ProviderCard
        name="Google Workspace"
        provider="google"
        info={data.google}
        scopeLabels={SCOPE_LABELS_GOOGLE}
        onReauthorize={handleReauthorize}
        relTime={relTime}
        onSyncNow={handleSyncNow}
        syncing={syncing}
      />
    </div>
  )
}

function ReadinessCard({ readiness }) {
  if (!readiness) return null
  const envEntries = Object.entries(readiness.env || {})
    .filter(([key]) => CORE_READINESS_ENV_KEYS.has(key))
  const redirects = Object.fromEntries(
    Object.entries(readiness.expected_redirect_uris || {})
      .filter(([provider]) => !['zoom', 'zoom_phone'].includes(provider))
  )

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-6">
      <h3 className="text-brand-ink font-sans text-base font-bold mb-1">Cloud Integration Readiness</h3>
      <p className="text-brand-ink-2 font-sans text-xs mb-4">
        Redacted setup status for Microsoft, Google, Teams, and cloud document callbacks.
      </p>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div>
          <h4 className="text-xs font-bold uppercase tracking-widest text-brand-muted mb-2">Environment</h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {envEntries.map(([key, value]) => (
              <div key={key} className="flex items-center justify-between gap-3 bg-brand-bg rounded-lg px-3 py-2">
                <span className="text-xs font-mono text-brand-ink truncate">{key}</span>
                <span className={`text-[10px] font-bold uppercase ${value.configured ? 'text-green-700' : 'text-red-600'}`}>
                  {value.configured ? 'Set' : 'Missing'}
                </span>
              </div>
            ))}
          </div>
        </div>
        <div>
          <h4 className="text-xs font-bold uppercase tracking-widest text-brand-muted mb-2">Expected Redirect URIs</h4>
          <div className="space-y-2">
            {Object.entries(redirects).map(([provider, uris]) => (
              <div key={provider} className="bg-brand-bg rounded-lg px-3 py-2">
                <div className="text-xs font-bold text-brand-ink capitalize mb-1">{provider}</div>
                {(uris || []).map((uri) => (
                  <div key={uri} className="text-[11px] font-mono text-brand-muted break-all">{uri}</div>
                ))}
              </div>
            ))}
          </div>
          <div className="mt-3 text-[11px] font-mono text-brand-muted bg-brand-bg px-3 py-2 rounded-lg break-all">
            {readiness.entra_verification_command}
          </div>
        </div>
      </div>
    </div>
  )
}

function SharePointBindingCard({ binding, onSaved, flash, onFlashClear }) {
  const [query, setQuery] = useState('')
  const [sites, setSites] = useState([])
  const [drives, setDrives] = useState([])
  const [siteId, setSiteId] = useState(binding?.site_id || '')
  const [siteWebUrl, setSiteWebUrl] = useState(binding?.site_web_url || '')
  const [driveId, setDriveId] = useState(binding?.drive_id || '')
  const [driveName, setDriveName] = useState(binding?.drive_name || '')
  const [loadingSites, setLoadingSites] = useState(false)
  const [loadingDrives, setLoadingDrives] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    setSiteId(binding?.site_id || '')
    setSiteWebUrl(binding?.site_web_url || '')
    setDriveId(binding?.drive_id || '')
    setDriveName(binding?.drive_name || '')
  }, [binding])

  useEffect(() => {
    if (!flash) return
    const timer = setTimeout(onFlashClear, 3000)
    return () => clearTimeout(timer)
  }, [flash, onFlashClear])

  const handleSearch = async () => {
    setLoadingSites(true)
    setError(null)
    try {
      const result = await listSharePointSites(query.trim() || undefined)
      setSites(result.items || [])
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to load SharePoint sites.')
    } finally {
      setLoadingSites(false)
    }
  }

  const handleSiteSelect = async (value) => {
    setSiteId(value)
    setDriveId('')
    setDriveName('')
    const site = sites.find((item) => item.id === value)
    setSiteWebUrl(site?.web_url || '')
    if (!value) return
    setLoadingDrives(true)
    setError(null)
    try {
      const result = await listSharePointDrives(value)
      setDrives(result.items || [])
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to load document libraries.')
    } finally {
      setLoadingDrives(false)
    }
  }

  const handleDriveSelect = (value) => {
    setDriveId(value)
    const drive = drives.find((item) => item.id === value)
    setDriveName(drive?.name || '')
  }

  const handleSave = async () => {
    if (!siteId || !driveId) return
    setSaving(true)
    setError(null)
    try {
      const result = await saveSharePointBinding({
        site_id: siteId,
        site_web_url: siteWebUrl,
        drive_id: driveId,
        drive_name: driveName || 'Documents',
        root_item_id: 'root',
        folder_path: '/',
        is_primary: true,
      })
      onSaved(result.binding)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to save SharePoint binding.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-6">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <h3 className="text-brand-ink font-sans text-base font-bold mb-1">SharePoint Storage Binding</h3>
          <p className="text-brand-ink-2 font-sans text-xs">
            Select the SharePoint site and document library used for matter folders and uploads.
          </p>
        </div>
        {binding?.drive_id && (
          <span className="shrink-0 text-[10px] font-bold uppercase text-green-700 bg-green-100 px-2 py-1 rounded-full">
            Configured
          </span>
        )}
      </div>

      {flash && (
        <div className="mb-3 px-3 py-2 bg-green-50 border border-green-200 text-green-700 rounded-lg text-xs font-medium">
          {flash}
        </div>
      )}
      {error && (
        <div className="mb-3 px-3 py-2 bg-red-50 border border-red-200 text-red-700 rounded-lg text-xs font-medium">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-3 mb-3">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search sites, or leave blank for root site"
          className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-brand-ink font-sans text-sm focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
        />
        <button
          onClick={handleSearch}
          disabled={loadingSites}
          className="px-4 py-2 border border-brand-line text-brand-ink font-sans text-xs font-medium rounded-lg hover:bg-brand-bg-soft transition-colors disabled:opacity-50"
        >
          {loadingSites ? 'Loading...' : 'Load sites'}
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <select
          value={siteId}
          onChange={(e) => handleSiteSelect(e.target.value)}
          className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-brand-ink font-sans text-sm focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
        >
          <option value="">Select site...</option>
          {siteId && !sites.some((site) => site.id === siteId) && (
            <option value={siteId}>{siteWebUrl || siteId}</option>
          )}
          {sites.map((site) => (
            <option key={site.id} value={site.id}>{site.name || site.web_url || site.id}</option>
          ))}
        </select>
        <select
          value={driveId}
          onChange={(e) => handleDriveSelect(e.target.value)}
          disabled={!siteId || loadingDrives}
          className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-brand-ink font-sans text-sm disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
        >
          <option value="">Select library...</option>
          {driveId && !drives.some((drive) => drive.id === driveId) && (
            <option value={driveId}>{driveName || driveId}</option>
          )}
          {drives.map((drive) => (
            <option key={drive.id} value={drive.id}>{drive.name || drive.id}</option>
          ))}
        </select>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving || !siteId || !driveId}
          className="px-4 py-2 bg-brand-ink text-white font-sans text-xs font-medium rounded-lg hover:bg-brand-ink/90 transition-colors disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save SharePoint binding'}
        </button>
        {binding?.drive_name && (
          <span className="text-xs text-brand-muted truncate">
            Current library: {binding.drive_name}
          </span>
        )}
      </div>
    </div>
  )
}

function ProviderCard({ name, provider, info, scopeLabels, onReauthorize, relTime, onSyncNow, syncing }) {
  const allScopes = [...(info.granted_scopes || []), ...(info.missing_required || [])]
  // Deduplicate while preserving order
  const uniqueScopes = [...new Set(allScopes)]
  const healthText = {
    healthy: 'Healthy',
    missing_scopes: 'Missing Scopes',
    refresh_failed: 'Refresh Failed',
    revoked: 'Reconnect Required',
    disconnected: 'Disconnected',
  }[info.health] || 'Needs Attention'

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-brand-ink font-sans text-base font-bold">{name}</h3>
          <span className={`inline-block mt-1 px-2.5 py-0.5 rounded-full text-xs font-bold ${
            info.health === 'healthy' ? 'bg-green-100 text-green-700' :
            info.health === 'missing_scopes' || info.health === 'refresh_failed' ? 'bg-amber-100 text-amber-700' :
            'bg-red-100 text-red-700'
          }`}>
            {healthText}
          </span>
          {info.connected && (
            <p className="mt-1 text-xs text-brand-ink-2 font-sans">
              {info.user_count ?? 0} users synced
              {info.last_sync_status === 'failed' ? ' · last sync failed' : ` · last run ${relTime(info.last_sync_at)}`}
            </p>
          )}
          {info.connected && (
            <p className="mt-1 text-xs text-brand-ink-2 font-sans">
              Token refresh {info.last_refresh_at ? relTime(info.last_refresh_at) : 'not yet recorded'}
            </p>
          )}
          {info.connected && info.last_sync_error && (
            <p className="mt-1 text-xs text-red-600 font-mono bg-red-50 px-2 py-1 rounded">
              {info.last_sync_error}
            </p>
          )}
          {info.connected && info.last_refresh_error && (
            <p className="mt-1 text-xs text-red-600 font-mono bg-red-50 px-2 py-1 rounded">
              {info.last_refresh_error}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {info.connected && (
            <button
              onClick={onSyncNow}
              disabled={syncing}
              className="px-4 py-2 border border-brand-line text-brand-ink font-sans text-xs font-medium rounded-lg hover:bg-brand-bg-soft transition-colors disabled:opacity-50"
            >
              {syncing ? 'Syncing…' : 'Sync now'}
            </button>
          )}
          <button
            onClick={() => onReauthorize(provider)}
            className="px-4 py-2 border border-brand-line text-brand-ink font-sans text-xs font-medium rounded-lg hover:bg-brand-bg-soft transition-colors"
          >
            {info.connected ? 'Re-authorize' : 'Connect'}
          </button>
        </div>
      </div>

      {info.recent_sync_runs?.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-4">
          {info.recent_sync_runs.slice(0, 3).map((run) => (
            <div key={`${run.job_type}-${run.started_at}`} className="bg-brand-bg border border-brand-line rounded-lg px-3 py-2">
              <div className="text-[11px] uppercase font-bold text-brand-ink-2">{run.job_type}</div>
              <div className={`text-xs font-bold ${run.status === 'completed' ? 'text-green-700' : 'text-red-600'}`}>
                {run.status} · {relTime(run.started_at)}
              </div>
              <div className="text-[11px] text-brand-ink-2">
                {run.items_ok || 0} ok · {run.items_failed || 0} failed
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-1.5">
        {uniqueScopes.map((scope) => {
          const granted = info.granted_scopes?.includes(scope)
          const label = scopeLabels[scope] || scope
          return (
            <div key={scope} className="flex items-center gap-2.5 px-3 py-2 rounded-lg bg-brand-bg">
              {granted ? (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M5 13l4 4L19 7" stroke="#16a34a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
              ) : (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6l12 12" stroke="#dc2626" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
              )}
              <span className={`text-sm ${granted ? 'text-brand-ink' : 'text-red-600'}`}>
                {label}
              </span>
              {!granted && (
                <span className="ml-auto text-xs text-red-500 font-medium">Required</span>
              )}
            </div>
          )
        })}
        {uniqueScopes.length === 0 && (
          <p className="text-brand-ink-2 font-sans text-sm py-2">Not connected. Grant access to enable integration features.</p>
        )}
      </div>
    </div>
  )
}
