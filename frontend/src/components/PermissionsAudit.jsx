import React, { useEffect, useState } from 'react'
import { getAdminPermissions } from '../api'

const SCOPE_LABELS_MS = {
  offline_access: 'Offline access (refresh tokens)',
  'User.Read.All': 'Read all user profiles',
  'Mail.Read': 'Read mail across organization',
  'Files.Read.All': 'Read all files (OneDrive + SharePoint)',
  'Sites.Read.All': 'Access SharePoint sites',
  'Calendars.ReadWrite': 'Read and write calendars',
}

const SCOPE_LABELS_GOOGLE = {
  'https://www.googleapis.com/auth/admin.directory.user.readonly': 'Read directory users',
  'https://www.googleapis.com/auth/gmail.readonly': 'Read Gmail messages',
  'https://www.googleapis.com/auth/drive.readonly': 'Read Google Drive files',
  'https://www.googleapis.com/auth/calendar': 'Read Google Calendar',
}

export default function PermissionsAudit() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

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
      <div className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl border text-sm font-bold ${overallColors[data.overall_health] || overallColors.disconnected}`}>
        <span className={`w-2 h-2 rounded-full ${
          data.overall_health === 'healthy' ? 'bg-green-500' :
          data.overall_health === 'attention_needed' ? 'bg-amber-500' : 'bg-red-500'
        }`} />
        Overall: {data.overall_health === 'healthy' ? 'Healthy' :
          data.overall_health === 'attention_needed' ? 'Needs Attention' : 'No integrations connected'}
      </div>

      {/* Microsoft card */}
      <ProviderCard
        name="Microsoft 365"
        provider="microsoft"
        info={data.microsoft}
        scopeLabels={SCOPE_LABELS_MS}
        onReauthorize={handleReauthorize}
      />

      {/* Google card */}
      <ProviderCard
        name="Google Workspace"
        provider="google"
        info={data.google}
        scopeLabels={SCOPE_LABELS_GOOGLE}
        onReauthorize={handleReauthorize}
      />
    </div>
  )
}

function ProviderCard({ name, provider, info, scopeLabels, onReauthorize }) {
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
        </div>
        <button
          onClick={() => onReauthorize(provider)}
          className="px-4 py-2 border border-brand-line text-brand-ink font-sans text-xs font-medium rounded-lg hover:bg-brand-bg-soft transition-colors"
        >
          {info.connected ? 'Re-authorize' : 'Connect'}
        </button>
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
