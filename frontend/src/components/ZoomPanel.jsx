import React, { useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  KeyRound,
  PhoneCall,
  PlugZap,
  RefreshCw,
  Save,
  ShieldCheck,
  Trash2,
  Unplug,
  Video,
  XCircle,
} from 'lucide-react'
import {
  clearZoomPhoneAppCredentials,
  connectZoomPhoneIntegration,
  connectZoomIntegration,
  disconnectZoomPhoneIntegration,
  disconnectZoomIntegration,
  getZoomPhoneStatus,
  getZoomStatus,
  saveZoomPhoneAppCredentials,
  testZoomPhoneIntegration,
} from '../api'

const PHONE_SCOPE_LABELS = {
  'phone:read:list_call_logs:admin': 'Read account call history',
  'phone:read:call_log:admin': 'Read call details',
}

export default function ZoomPanel() {
  const [status, setStatus] = useState(null)
  const [phoneStatus, setPhoneStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [phoneBusy, setPhoneBusy] = useState(false)
  const [flash, setFlash] = useState(null)
  const [appForm, setAppForm] = useState({
    client_id: '',
    client_secret: '',
    webhook_secret_token: '',
    zoom_account_id: '',
  })

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
    const savedAccountId = zoomPhoneData?.app_credentials?.zoom_account_id || ''
    setAppForm((current) => ({
      ...current,
      zoom_account_id: current.zoom_account_id || savedAccountId,
    }))
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

  const handleSavePhoneApp = async (event) => {
    event.preventDefault()
    setPhoneBusy(true)
    try {
      await saveZoomPhoneAppCredentials({
        ...appForm,
        zoom_account_id: appForm.zoom_account_id.trim(),
      })
      setAppForm({ client_id: '', client_secret: '', webhook_secret_token: '', zoom_account_id: '' })
      await loadPanel()
      showFlash('Zoom Phone app credentials saved.')
    } catch (err) {
      showFlash(err?.response?.data?.detail || 'Failed to save Zoom Phone app credentials.', 'error')
    } finally {
      setPhoneBusy(false)
    }
  }

  const handleClearPhoneApp = async () => {
    setPhoneBusy(true)
    try {
      await clearZoomPhoneAppCredentials()
      setAppForm({ client_id: '', client_secret: '', webhook_secret_token: '', zoom_account_id: '' })
      await loadPanel()
      showFlash('Zoom Phone app credentials cleared.')
    } catch (err) {
      showFlash(err?.response?.data?.detail || 'Failed to clear Zoom Phone app credentials.', 'error')
    } finally {
      setPhoneBusy(false)
    }
  }

  const handleCopy = async (text, label = 'Value') => {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      showFlash(`${label} copied.`)
    } catch {
      showFlash(`Could not copy ${label.toLowerCase()}.`, 'error')
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
  const tenantPhoneAppConfigured = Boolean(phoneStatus?.tenant_app_configured)
  const tenantPhoneAppSaved = Boolean(phoneStatus?.app_credentials?.configured)
  const phoneVerificationPending = phoneStatus?.status === 'account_verification_required'

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
              status={phoneVerificationPending
                ? 'attention'
                : phoneConnected && phoneMissingScopes.length === 0 ? 'healthy' : phoneConfigured ? 'attention' : 'missing'}
              label={phoneStatus?.status === 'missing_scopes' || phoneStatus?.status === 'reauthorization_required'
                ? 'Phone needs re-authorization'
                : phoneVerificationPending
                  ? 'Phone account proof pending'
                : phoneConnected
                  ? 'Phone grant connected'
                  : phoneConfigured
                    ? 'Phone ready to connect'
                    : tenantPhoneAppSaved ? 'Add Phone Account ID' : 'Add Phone app'}
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
            label: phoneVerificationPending || phoneConnected ? 'Re-authorize Phone' : 'Connect Zoom Phone',
            onClick: connectZoomPhoneIntegration,
            disabled: phoneBusy || !phoneConfigured,
            icon: PlugZap,
          }}
          secondaryActions={phoneConnected && !phoneVerificationPending ? [
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
          footer={phoneVerificationPending
            ? 'OAuth is saved; signed-event and provider-fetch proof is still required.'
            : phoneConnected ? 'Access refreshes automatically during sync and connection tests.' : null}
        >
          {!phoneConfigured && (
            <SetupNotice
              tone="info"
              title={tenantPhoneAppSaved ? 'Add this firm’s Zoom Account ID' : 'Add your firm’s Zoom OAuth app'}
              body={tenantPhoneAppSaved
                ? 'The app secrets are saved. Add the exact Account ID from Zoom Account Profile to finish the tenant binding, then connect Zoom Phone.'
                : 'Create a Zoom OAuth app for this tenant, add the callback URL below, save the Account ID and app credentials, then connect Zoom Phone.'}
            />
          )}
          <ZoomPhoneAppSetup
            status={phoneStatus}
            form={appForm}
            setForm={setAppForm}
            busy={phoneBusy}
            onSave={handleSavePhoneApp}
            onClear={handleClearPhoneApp}
            onCopy={handleCopy}
            tenantConfigured={tenantPhoneAppConfigured}
            appSaved={tenantPhoneAppSaved}
          />
          {phoneVerificationPending && (
            <SetupNotice
              tone="warning"
              title="Place a Zoom Phone test call"
              body="OAuth is saved, but call import and Test connection stay blocked. A correctly signed v3 call-element event must match the saved Account ID, then the pending grant must successfully fetch that exact call from Zoom before verification completes. Re-authorize if the grant is wrong or revoked."
            />
          )}
          {phoneConnected && phoneMissingScopes.length > 0 && (
            <SetupNotice
              tone="warning"
              title="Re-authorization required"
              body="The current Zoom grant is missing Phone permissions. Re-authorize with the updated scope set."
            />
          )}
          {phoneConnected && phoneMissingScopes.length === 0 && (
            <SetupNotice
              tone="info"
              title="Authorization stays connected"
              body="Zoom access tokens last about one hour, but Clarity uses the saved refresh token to get a new access token when syncing or testing. Re-authorize only if Zoom access is revoked, scopes change, or the grant goes unused long enough for Zoom to expire the refresh token."
            />
          )}
          <ScopeChecklist
            required={phoneStatus?.required_scopes || []}
            missing={phoneMissingScopes}
          />
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
  if (!phoneConfigured) return phoneStatus?.app_credentials?.configured ? 'Add Account ID' : 'Add Zoom app'
  if (phoneStatus?.status === 'missing_scopes') return 'Missing permissions'
  if (phoneStatus?.status === 'reauthorization_required') return 'Re-authorization required'
  if (phoneStatus?.status === 'account_verification_required') return 'Account proof pending'
  if (!phoneConnected) return 'Ready to connect'
  if (phoneStatus?.missing_scopes?.length > 0) return 'Missing permissions'
  return 'Connected'
}

function phoneStatusTone(phoneStatus, phoneConfigured, phoneConnected) {
  if (!phoneConfigured) return phoneStatus?.app_credentials?.configured ? 'attention' : 'neutral'
  if (phoneStatus?.status === 'missing_scopes') return 'warning'
  if (phoneStatus?.status === 'reauthorization_required') return 'attention'
  if (phoneStatus?.status === 'account_verification_required') return 'attention'
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

function ZoomPhoneAppSetup({
  status,
  form,
  setForm,
  busy,
  onSave,
  onClear,
  onCopy,
  tenantConfigured,
  appSaved,
}) {
  const callbackUrl = status?.redirect_uri || status?.app_credentials?.redirect_uri
  const webhookUrl = status?.webhook_url || status?.app_credentials?.webhook_url
  const webhookEvents = 'phone.callee_call_element_completed\nphone.caller_call_element_completed'
  const clientIdHint = status?.app_credentials?.client_id_hint
  const savedAccountId = status?.app_credentials?.zoom_account_id || ''
  const hasOAuthPair = form.client_id.trim() && form.client_secret.trim()
  const hasPartialOAuth = Boolean(form.client_id.trim()) !== Boolean(form.client_secret.trim())
  const hasWebhookSecret = Boolean(form.webhook_secret_token.trim())
  const hasAccountId = Boolean(form.zoom_account_id.trim())
  const accountIdChanged = form.zoom_account_id.trim() !== savedAccountId
  const hasUpdate = appSaved
    ? hasOAuthPair || hasWebhookSecret || accountIdChanged
    : hasOAuthPair && hasWebhookSecret
  const canSave = !busy && hasAccountId && !hasPartialOAuth && hasUpdate

  return (
    <div className="rounded-lg border border-brand-line bg-brand-bg overflow-hidden">
      <div className="px-4 py-3 border-b border-brand-line flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-lg bg-brand-surface border border-brand-line flex items-center justify-center text-brand-ink">
            <KeyRound size={17} />
          </div>
          <div>
            <div className="text-sm font-bold text-brand-ink">Customer Zoom app</div>
            <div className="text-xs text-brand-ink-2 mt-0.5">
              {tenantConfigured
                ? `Saved${clientIdHint ? ` as ${clientIdHint}` : ''}. The non-secret Account ID below binds this tenant to exactly one Zoom account.`
                : appSaved
                  ? `App secrets saved${clientIdHint ? ` as ${clientIdHint}` : ''}. Add the Account ID to complete its tenant binding.`
                  : 'Save the Zoom Account ID, OAuth client ID, client secret, and webhook token from this firm-owned app.'}
            </div>
          </div>
        </div>
        <StatusBadge
          status={tenantConfigured ? 'healthy' : appSaved ? 'attention' : 'missing'}
          label={tenantConfigured ? 'Tenant app bound' : appSaved ? 'Account ID required' : 'Tenant app required'}
        />
      </div>

      <div className="p-4 space-y-4">
        {callbackUrl && (
          <div>
            <label className="block text-[11px] font-bold uppercase tracking-wider text-brand-muted mb-1">
              Zoom callback URL
            </label>
            <div className="flex gap-2">
              <code className="flex-1 min-w-0 rounded-lg bg-brand-surface border border-brand-line px-3 py-2 text-[11px] text-brand-ink break-all">
                {callbackUrl}
              </code>
              <button
                type="button"
                onClick={() => onCopy(callbackUrl, 'Callback URL')}
                className="w-10 h-10 inline-flex items-center justify-center rounded-lg border border-brand-line text-brand-ink hover:bg-brand-surface"
                title="Copy callback URL"
              >
                <Copy size={15} />
              </button>
            </div>
          </div>
        )}
        {webhookUrl && (
          <div>
            <label className="block text-[11px] font-bold uppercase tracking-wider text-brand-muted mb-1">
              Zoom webhook URL
            </label>
            <div className="flex gap-2">
              <code className="flex-1 min-w-0 rounded-lg bg-brand-surface border border-brand-line px-3 py-2 text-[11px] text-brand-ink break-all">
                {webhookUrl}
              </code>
              <button
                type="button"
                onClick={() => onCopy(webhookUrl, 'Webhook URL')}
                className="w-10 h-10 inline-flex items-center justify-center rounded-lg border border-brand-line text-brand-ink hover:bg-brand-surface"
                title="Copy webhook URL"
              >
                <Copy size={15} />
              </button>
            </div>
          </div>
        )}
        {webhookUrl && (
          <div>
            <label className="block text-[11px] font-bold uppercase tracking-wider text-brand-muted mb-1">
              Required Zoom Phone events (v3)
            </label>
            <div className="flex gap-2">
              <code className="flex-1 min-w-0 whitespace-pre-wrap rounded-lg bg-brand-surface border border-brand-line px-3 py-2 text-[11px] text-brand-ink break-all">
                {webhookEvents}
              </code>
              <button
                type="button"
                onClick={() => onCopy(webhookEvents, 'Required event list')}
                className="w-10 h-10 inline-flex items-center justify-center rounded-lg border border-brand-line text-brand-ink hover:bg-brand-surface"
                title="Copy required Zoom Phone events"
              >
                <Copy size={15} />
              </button>
            </div>
            <p className="mt-1 text-[11px] text-brand-muted">
              Select both call-element events in Zoom. Call-history-completed events are legacy compatibility only.
            </p>
          </div>
        )}

        <form onSubmit={onSave} className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <label className="block lg:col-span-2">
            <span className="block text-[11px] font-bold uppercase tracking-wider text-brand-muted mb-1">
              Zoom Account ID <span aria-hidden="true">*</span>
            </span>
            <input
              required
              autoComplete="off"
              minLength={8}
              maxLength={255}
              pattern="[A-Za-z0-9_-]+"
              title="Use 8–255 letters, numbers, hyphens, or underscores."
              value={form.zoom_account_id}
              onChange={(event) => setForm((current) => ({ ...current, zoom_account_id: event.target.value }))}
              placeholder="Account ID from Zoom Account Profile"
              aria-describedby="zoom-phone-account-id-help zoom-phone-account-id-status"
              className="w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2 text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
            />
            <span id="zoom-phone-account-id-help" className="mt-1 block text-[11px] text-brand-muted">
              In Zoom, open Account Management → Account Profile and copy the Account ID. It is not a secret. Entering it explicitly avoids requesting a Zoom user-profile scope.
            </span>
            <span
              id="zoom-phone-account-id-status"
              className={`mt-1 block text-[11px] font-semibold ${savedAccountId && form.zoom_account_id.trim() === savedAccountId ? 'text-green-700' : 'text-amber-700'}`}
            >
              {savedAccountId && form.zoom_account_id.trim() === savedAccountId
                ? 'Account binding saved. A signed v3 event plus a successful provider fetch of that exact call must prove this Account ID before imports are enabled.'
                : savedAccountId
                  ? 'New Account ID is not saved. Saving it disconnects the current grant and requires re-authorization.'
                  : 'Required before this tenant can connect Zoom Phone.'}
            </span>
          </label>
          <label className="block">
            <span className="block text-[11px] font-bold uppercase tracking-wider text-brand-muted mb-1">
              Zoom OAuth client ID
            </span>
            <input
              value={form.client_id}
              onChange={(event) => setForm((current) => ({ ...current, client_id: event.target.value }))}
              placeholder={clientIdHint ? `Current: ${clientIdHint}` : 'Client ID'}
              className="w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2 text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
            />
          </label>
          <label className="block">
            <span className="block text-[11px] font-bold uppercase tracking-wider text-brand-muted mb-1">
              Zoom OAuth client secret
            </span>
            <input
              type="password"
              value={form.client_secret}
              onChange={(event) => setForm((current) => ({ ...current, client_secret: event.target.value }))}
              placeholder={appSaved ? 'Enter to replace saved secret' : 'Client secret'}
              className="w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2 text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
            />
          </label>
          <label className="block lg:col-span-2">
            <span className="block text-[11px] font-bold uppercase tracking-wider text-brand-muted mb-1">
              Zoom webhook secret token
            </span>
            <input
              type="password"
              value={form.webhook_secret_token}
              onChange={(event) => setForm((current) => ({ ...current, webhook_secret_token: event.target.value }))}
              placeholder={status?.webhook_secret_configured ? 'Enter to replace saved webhook secret token' : 'Secret token from Zoom app Webhook settings'}
              className="w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2 text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
            />
            {status?.webhook_secret_configured && (
              <span className="mt-1 block text-[11px] font-semibold text-green-700">Webhook signing is configured.</span>
            )}
          </label>
          <div className="lg:col-span-2 flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={!canSave}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-brand-ink text-white font-sans text-xs font-bold hover:bg-brand-ink/90 disabled:opacity-45 disabled:cursor-not-allowed"
            >
              <Save size={14} />
              Save Zoom app
            </button>
            {appSaved && (
              <button
                type="button"
                onClick={onClear}
                disabled={busy}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-brand-line text-brand-ink font-sans text-xs font-bold hover:bg-brand-surface disabled:opacity-45 disabled:cursor-not-allowed"
              >
                <Trash2 size={14} />
                Clear tenant app
              </button>
            )}
          </div>
        </form>
      </div>
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
