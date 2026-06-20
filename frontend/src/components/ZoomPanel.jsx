import React, { useEffect, useState } from 'react'
import {
  connectZoomIntegration,
  disconnectZoomIntegration,
  getIntegrationReadiness,
  getZoomStatus,
} from '../api'

const ZOOM_ENV_KEYS = new Set([
  'ZOOM_CLIENT_ID',
  'ZOOM_CLIENT_SECRET',
  'ZOOM_REDIRECT_URI',
])

export default function ZoomPanel() {
  const [status, setStatus] = useState(null)
  const [readiness, setReadiness] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState(null)

  const showFlash = (text, type = 'success') => {
    setFlash({ text, type })
    setTimeout(() => setFlash(null), 4000)
  }

  const loadPanel = async () => {
    const [zoomData, readinessData] = await Promise.all([
      getZoomStatus().catch(() => ({ configured: false, connected: false })),
      getIntegrationReadiness().catch(() => null),
    ])
    setStatus(zoomData)
    setReadiness(readinessData)
  }

  useEffect(() => {
    loadPanel()
      .catch(() => showFlash('Failed to load Zoom integration status.', 'error'))
      .finally(() => setLoading(false))
  }, [])

  const handleDisconnect = async () => {
    setBusy(true)
    try {
      await disconnectZoomIntegration()
      await loadPanel()
      showFlash('Zoom disconnected.')
    } catch (err) {
      showFlash(err?.response?.data?.detail || 'Failed to disconnect Zoom.', 'error')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <div className="w-8 h-8 border-4 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const configured = Boolean(status?.configured)
  const connected = Boolean(status?.connected)
  const envEntries = Object.entries(readiness?.env || {})
    .filter(([key]) => ZOOM_ENV_KEYS.has(key))
  const zoomRedirects = readiness?.expected_redirect_uris?.zoom || []

  if (!configured) {
    return (
      <div className="space-y-6">
        {flash && <FlashMessage flash={flash} />}
        <GateCard
          title="Set up Zoom when a firm needs it"
          body="Zoom meeting links are optional and no longer affect Microsoft or Google cloud integration readiness. Add the Zoom OAuth credentials, then return here to connect it."
        />
        <ZoomSetupCard envEntries={envEntries} redirectUris={zoomRedirects} />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {flash && <FlashMessage flash={flash} />}

      <div className="flex flex-wrap items-center gap-3">
        <span className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl border text-sm font-bold ${
          connected
            ? 'bg-green-100 text-green-700 border-green-200'
            : 'bg-amber-100 text-amber-700 border-amber-200'
        }`}>
          <span className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-amber-500'}`} />
          {connected ? 'Zoom connected' : 'Zoom ready to connect'}
        </span>
      </div>

      <div className="bg-brand-surface border border-brand-line rounded-xl p-6 max-w-3xl">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h3 className="text-brand-ink font-sans text-base font-bold mb-1">Zoom meetings</h3>
            <p className="text-brand-ink-2 font-sans text-sm">
              Use Zoom only for firms that want Zoom meeting links in calendar events. Microsoft and Google setup stays separate.
            </p>
            {connected && (
              <p className="text-xs text-brand-ink-2 font-sans mt-3">
                Connected as {status.connection_type === 'tenant' ? 'tenant-wide' : 'current user'}
                {status.expires_at ? ` - expires ${new Date(status.expires_at).toLocaleDateString()}` : ''}
              </p>
            )}
          </div>
          {connected ? (
            <button
              onClick={handleDisconnect}
              disabled={busy}
              className="px-4 py-2 border border-brand-line text-brand-ink font-sans text-xs font-medium rounded-lg hover:bg-brand-bg-soft transition-colors disabled:opacity-50"
            >
              {busy ? 'Disconnecting...' : 'Disconnect Zoom'}
            </button>
          ) : (
            <button
              onClick={() => connectZoomIntegration('admin')}
              disabled={busy}
              className="px-4 py-2 bg-brand-ink text-white font-sans text-xs font-medium rounded-lg hover:bg-brand-ink/90 transition-colors disabled:opacity-50"
            >
              Connect Zoom
            </button>
          )}
        </div>
      </div>

      <ZoomSetupCard envEntries={envEntries} redirectUris={zoomRedirects} />
    </div>
  )
}

function FlashMessage({ flash }) {
  return (
    <div className={`px-4 py-3 rounded-xl text-xs font-medium ${
      flash.type === 'success'
        ? 'bg-green-50 border border-green-200 text-green-700'
        : 'bg-red-50 border border-red-200 text-red-700'
    }`}>
      {flash.text}
    </div>
  )
}

function GateCard({ title, body }) {
  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-6 max-w-2xl">
      <h3 className="text-brand-ink font-sans text-base font-bold mb-1">{title}</h3>
      <p className="text-brand-ink-2 font-sans text-sm">{body}</p>
    </div>
  )
}

function ZoomSetupCard({ envEntries, redirectUris }) {
  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-6 max-w-3xl">
      <h3 className="text-brand-ink font-sans text-base font-bold mb-1">Zoom OAuth setup</h3>
      <p className="text-brand-ink-2 font-sans text-xs mb-4">
        Configuration status for the optional Zoom provider.
      </p>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div>
          <h4 className="text-xs font-bold uppercase tracking-widest text-brand-muted mb-2">Environment</h4>
          <div className="space-y-2">
            {envEntries.map(([key, value]) => (
              <div key={key} className="flex items-center justify-between gap-3 bg-brand-bg rounded-lg px-3 py-2">
                <span className="text-xs font-mono text-brand-ink truncate">{key}</span>
                <span className={`text-[10px] font-bold uppercase ${value.configured ? 'text-green-700' : 'text-brand-muted'}`}>
                  {value.configured ? 'Set' : 'Optional'}
                </span>
              </div>
            ))}
          </div>
        </div>
        <div>
          <h4 className="text-xs font-bold uppercase tracking-widest text-brand-muted mb-2">Redirect URI</h4>
          <div className="space-y-2">
            {redirectUris.length > 0 ? (
              redirectUris.map((uri) => (
                <div key={uri} className="bg-brand-bg rounded-lg px-3 py-2 text-[11px] font-mono text-brand-muted break-all">
                  {uri}
                </div>
              ))
            ) : (
              <div className="bg-brand-bg rounded-lg px-3 py-2 text-xs text-brand-muted">
                No Zoom redirect URI reported.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
