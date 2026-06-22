import React, { useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  PhoneCall,
  PlugZap,
  RefreshCw,
  ShieldCheck,
  Unplug,
  Video,
  XCircle,
} from 'lucide-react'
import {
  connectZoomPhoneIntegration,
  connectZoomIntegration,
  disconnectZoomPhoneIntegration,
  disconnectZoomIntegration,
  getZoomPhoneStatus,
  getZoomStatus,
  testZoomPhoneIntegration,
} from '../api'

const PHONE_SCOPE_LABELS = {
  'phone:read:list_call_logs:admin': 'Read account call history',
  'phone:read:call_log:admin': 'Read call details',
  'phone:read:list_call_recordings:admin': 'List call recordings',
  'phone:read:call_recording:admin': 'Read recording metadata',
  'phone:read:recording_transcript:admin': 'Read recording transcripts',
}

export default function ZoomPanel() {
  const [status, setStatus] = useState(null)
  const [phoneStatus, setPhoneStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [phoneBusy, setPhoneBusy] = useState(false)
  const [flash, setFlash] = useState(null)

  const showFlash = (text, type = 'success') => {
    setFlash({ text, type })
    setTimeout(() => setFlash(null), 4000)
  }

  const loadPanel = async () => {
    const [zoomData, zoomPhoneData] = await Promise.all([
      getZoomStatus().catch(() => ({ configured: false, connected: false })),
      getZoomPhoneStatus().catch(() => ({ configured: false, connected: false, status: 'not_configured' })),
    ])
    setStatus(zoomData)
    setPhoneStatus(zoomPhoneData)
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

  const handlePhoneTest = async () => {
    setPhoneBusy(true)
    try {
      const result = await testZoomPhoneIntegration()
      await loadPanel()
      showFlash(`Zoom Phone connection works. Sample calls found: ${result.sample_count ?? 0}.`)
    } catch (err) {
      showFlash(err?.response?.data?.detail || 'Zoom Phone connection test failed.', 'error')
    } finally {
      setPhoneBusy(false)
    }
  }

  const handlePhoneDisconnect = async () => {
    setPhoneBusy(true)
    try {
      await disconnectZoomPhoneIntegration()
      await loadPanel()
      showFlash('Zoom Phone disconnected.')
    } catch (err) {
      showFlash(err?.response?.data?.detail || 'Failed to disconnect Zoom Phone.', 'error')
    } finally {
      setPhoneBusy(false)
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
  const phoneConfigured = Boolean(phoneStatus?.configured)
  const phoneConnected = Boolean(phoneStatus?.connected)
  const phoneMissingScopes = phoneStatus?.missing_scopes || []

  return (
    <div className="space-y-6">
      {flash && <FlashMessage flash={flash} />}

      <div className="bg-brand-surface border border-brand-line rounded-xl p-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <p className="text-xs font-bold uppercase tracking-widest text-brand-muted mb-2">Zoom integrations</p>
            <h2 className="text-brand-ink font-serif text-2xl font-bold tracking-tight">Connect Zoom services</h2>
            <p className="text-brand-ink-2 font-sans text-sm mt-2 max-w-2xl">
              Phone intake and meeting links use separate tenant grants so reception workflows stay independent from calendar meeting setup.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <StatusBadge
              status={phoneConnected && phoneMissingScopes.length === 0 ? 'healthy' : phoneConfigured ? 'attention' : 'missing'}
              label={phoneConnected ? 'Phone grant connected' : phoneConfigured ? 'Phone ready to connect' : 'Phone app pending'}
            />
            <StatusBadge
              status={connected ? 'healthy' : configured ? 'attention' : 'neutral'}
              label={connected ? 'Meetings connected' : configured ? 'Meetings ready' : 'Meetings optional'}
            />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <IntegrationCard
          icon={PhoneCall}
          title="Zoom Phone intake"
          subtitle="Import customer call history into the intake dashboard."
          status={phoneStatusLabel(phoneStatus, phoneConfigured, phoneConnected)}
          statusTone={phoneStatusTone(phoneStatus, phoneConfigured, phoneConnected)}
          primaryAction={{
            label: phoneConnected ? 'Re-authorize Phone' : 'Connect Zoom Phone',
            onClick: connectZoomPhoneIntegration,
            disabled: phoneBusy || !phoneConfigured,
            icon: PlugZap,
          }}
          secondaryActions={phoneConnected ? [
            {
              label: phoneBusy ? 'Testing...' : 'Test connection',
              onClick: handlePhoneTest,
              disabled: phoneBusy,
              icon: RefreshCw,
            },
            {
              label: 'Disconnect',
              onClick: handlePhoneDisconnect,
              disabled: phoneBusy,
              icon: Unplug,
            },
          ] : []}
          footer={phoneConnected && phoneStatus?.expires_at ? `Token expires ${new Date(phoneStatus.expires_at).toLocaleString()}` : null}
        >
          {!phoneConfigured && (
            <SetupNotice
              tone="info"
              title="Zoom Phone is waiting on platform setup"
              body="Once Clarity enables the shared Zoom app, your Zoom account admin can grant Phone access here."
            />
          )}
          {phoneConnected && phoneMissingScopes.length > 0 && (
            <SetupNotice
              tone="warning"
              title="Re-authorization required"
              body="The current Zoom grant is missing Phone permissions. Re-authorize with the updated scope set."
            />
          )}
          {(phoneConfigured || phoneConnected) && (
            <ScopeChecklist
              required={phoneStatus?.required_scopes || []}
              missing={phoneMissingScopes}
            />
          )}
        </IntegrationCard>

        <IntegrationCard
          icon={Video}
          title="Zoom meetings"
          subtitle="Create Zoom meeting links for calendar events when a firm uses Zoom."
          status={connected ? 'Connected' : configured ? 'Ready to connect' : 'Optional setup'}
          statusTone={connected ? 'healthy' : configured ? 'attention' : 'neutral'}
          primaryAction={{
            label: connected ? 'Re-authorize Zoom' : 'Connect Zoom',
            onClick: () => connectZoomIntegration('admin'),
            disabled: busy || !configured,
            icon: PlugZap,
          }}
          secondaryActions={connected ? [
            {
              label: busy ? 'Disconnecting...' : 'Disconnect',
              onClick: handleDisconnect,
              disabled: busy,
              icon: Unplug,
            },
          ] : []}
          footer={connected && status?.expires_at ? `Grant expires ${new Date(status.expires_at).toLocaleDateString()}` : null}
        >
          {!configured && (
            <SetupNotice
              tone="info"
              title="Meeting links are optional"
              body="Zoom Meetings can remain unavailable when the tenant only needs Phone intake."
            />
          )}
          {connected && (
            <div className="rounded-lg bg-brand-bg px-3 py-2 text-xs text-brand-ink-2">
              Connected as {status.connection_type === 'tenant' ? 'tenant-wide' : 'current user'}.
            </div>
          )}
        </IntegrationCard>
      </div>

    </div>
  )
}

function phoneStatusLabel(phoneStatus, phoneConfigured, phoneConnected) {
  if (!phoneConfigured) return 'Platform setup pending'
  if (!phoneConnected) return 'Ready to connect'
  if (phoneStatus?.missing_scopes?.length > 0) return 'Missing permissions'
  return 'Connected'
}

function phoneStatusTone(phoneStatus, phoneConfigured, phoneConnected) {
  if (!phoneConfigured) return 'neutral'
  if (!phoneConnected) return 'attention'
  if (phoneStatus?.missing_scopes?.length > 0) return 'warning'
  return 'healthy'
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

function StatusBadge({ status, label }) {
  const styles = {
    healthy: 'bg-green-100 text-green-700 border-green-200',
    attention: 'bg-amber-100 text-amber-700 border-amber-200',
    warning: 'bg-amber-100 text-amber-700 border-amber-200',
    missing: 'bg-red-50 text-red-700 border-red-200',
    neutral: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  }
  const dots = {
    healthy: 'bg-green-500',
    attention: 'bg-amber-500',
    warning: 'bg-amber-500',
    missing: 'bg-red-500',
    neutral: 'bg-brand-muted',
  }
  return (
    <span className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-bold ${styles[status] || styles.neutral}`}>
      <span className={`w-2 h-2 rounded-full ${dots[status] || dots.neutral}`} />
      {label}
    </span>
  )
}

function IntegrationCard({
  icon: Icon,
  title,
  subtitle,
  status,
  statusTone,
  primaryAction,
  secondaryActions = [],
  footer,
  children,
}) {
  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-6 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="w-10 h-10 rounded-xl bg-brand-ink text-white flex items-center justify-center shrink-0">
            <Icon size={18} />
          </div>
          <div>
            <h3 className="text-brand-ink font-sans text-base font-bold">{title}</h3>
            <p className="text-brand-ink-2 font-sans text-sm mt-1">{subtitle}</p>
          </div>
        </div>
        <StatusBadge status={statusTone} label={status} />
      </div>

      <div className="mt-5 space-y-3">
        {children}
      </div>

      <div className="mt-5 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex flex-wrap gap-2">
          <ActionButton action={primaryAction} primary />
          {secondaryActions.map((action) => (
            <ActionButton key={action.label} action={action} />
          ))}
        </div>
        {footer && <span className="text-xs text-brand-muted font-sans">{footer}</span>}
      </div>
    </div>
  )
}

function ActionButton({ action, primary = false }) {
  const Icon = action.icon
  return (
    <button
      onClick={action.onClick}
      disabled={action.disabled}
      className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg font-sans text-xs font-bold transition-colors disabled:opacity-45 disabled:cursor-not-allowed ${
        primary
          ? 'bg-brand-ink text-white hover:bg-brand-ink/90'
          : 'border border-brand-line text-brand-ink hover:bg-brand-bg-soft'
      }`}
    >
      {Icon && <Icon size={14} />}
      {action.label}
    </button>
  )
}

function SetupNotice({ tone, title, body }) {
  const styles = {
    warning: {
      wrap: 'bg-amber-50 border-amber-200 text-amber-900',
      icon: AlertTriangle,
    },
    info: {
      wrap: 'bg-brand-bg border-brand-line text-brand-ink',
      icon: ShieldCheck,
    },
  }
  const style = styles[tone] || styles.info
  const Icon = style.icon
  return (
    <div className={`rounded-lg border px-3 py-3 ${style.wrap}`}>
      <div className="flex gap-2">
        <Icon size={16} className="mt-0.5 shrink-0" />
        <div>
          <div className="text-xs font-bold">{title}</div>
          <div className="text-xs mt-0.5 opacity-90">{body}</div>
        </div>
      </div>
    </div>
  )
}

function ScopeChecklist({ required, missing }) {
  const requiredScopes = required.length > 0 ? required : Object.keys(PHONE_SCOPE_LABELS)
  const missingSet = new Set(missing)
  return (
    <div className="rounded-lg border border-brand-line overflow-hidden">
      <div className="px-3 py-2 bg-brand-bg-soft border-b border-brand-line flex items-center justify-between gap-3">
        <span className="text-xs font-bold text-brand-ink">Phone access included</span>
        <span className="text-[10px] font-bold uppercase text-brand-muted">{requiredScopes.length - missingSet.size}/{requiredScopes.length} granted</span>
      </div>
      <div className="divide-y divide-brand-line">
        {requiredScopes.map((scope) => {
          const isMissing = missingSet.has(scope)
          const Icon = isMissing ? XCircle : CheckCircle2
          return (
            <div key={scope} className="flex items-center gap-2 px-3 py-2 bg-brand-bg">
              <Icon size={15} className={isMissing ? 'text-red-600' : 'text-green-600'} />
              <span className={`text-xs ${isMissing ? 'text-red-700' : 'text-brand-ink'}`}>
                {PHONE_SCOPE_LABELS[scope] || scope}
              </span>
              {isMissing && <span className="ml-auto text-[10px] font-bold uppercase text-red-600">Missing</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}
