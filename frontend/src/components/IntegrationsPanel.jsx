import React, { useEffect, useState } from 'react'
import { getAdminPermissions, triggerUserSync, retryCloudInit } from '../api'

const SCOPE_LABELS_MS = {
  offline_access: 'Offline access (refresh tokens)',
  'User.Read.All': 'Read all user profiles',
  'Mail.Read': 'Read mail across organization',
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
  'https://www.googleapis.com/auth/admin.directory.user.readonly': 'Read directory users',
  'https://www.googleapis.com/auth/gmail.readonly': 'Read Gmail messages',
  'https://www.googleapis.com/auth/drive.readonly': 'Read Google Drive files (read-only)',
  'https://www.googleapis.com/auth/drive': 'Read & write Google Drive (folders + files)',
  'https://www.googleapis.com/auth/calendar': 'Read & write Google Calendar',
}

export default function IntegrationsPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [retryResult, setRetryResult] = useState(null)

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

  useEffect(() => {
    getAdminPermissions()
      .then(setData)
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
      window.location.href = `${window.location.origin}/api/integrations/microsoft/connect?intent=${intent}`
    } else {
      window.location.href = `${window.location.origin}/api/integrations/google/connect?intent=${intent}`
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
        <div className="flex items-center gap-3">
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

function ProviderCard({ name, provider, info, scopeLabels, onReauthorize, relTime, onSyncNow, syncing }) {
  const allScopes = [...(info.granted_scopes || []), ...(info.missing_required || [])]
  // Deduplicate while preserving order
  const uniqueScopes = [...new Set(allScopes)]

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-brand-ink font-sans text-base font-bold">{name}</h3>
          <span className={`inline-block mt-1 px-2.5 py-0.5 rounded-full text-xs font-bold ${
            info.health === 'healthy' ? 'bg-green-100 text-green-700' :
            info.health === 'missing_scopes' ? 'bg-amber-100 text-amber-700' :
            'bg-red-100 text-red-700'
          }`}>
            {info.health === 'healthy' ? 'Healthy' :
             info.health === 'missing_scopes' ? 'Missing Scopes' : 'Disconnected'}
          </span>
          {info.connected && (
            <p className="mt-1 text-xs text-brand-ink-2 font-sans">
              {info.user_count ?? 0} users synced
              {info.last_sync_status === 'failed' ? ' · last sync failed' : ` · last run ${relTime(info.last_sync_at)}`}
            </p>
          )}
          {info.connected && info.last_sync_error && (
            <p className="mt-1 text-xs text-red-600 font-mono bg-red-50 px-2 py-1 rounded">
              {info.last_sync_error}
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
