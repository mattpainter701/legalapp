import { useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  ExternalLink,
  PhoneCall,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'
import {
  createTeamsVoiceSubscription,
  deleteTeamsVoiceSubscription,
  syncTeamsVoiceCalls,
  testTeamsVoiceConnection,
  updateTeamsVoiceSettings,
} from '../../api'
import { errorText } from './teamsErrors'

const when = (value) => (value ? new Date(value).toLocaleString() : null)

// Setup is genuinely three separate acts in Microsoft's model — name the
// directory, have an admin consent to the application permission, then let
// Graph validate our notification endpoint. Showing them as an ordered
// checklist is what keeps an admin from enabling capture and then wondering
// why nothing arrives.
function Step({ index, title, done, children, hint }) {
  return (
    <div className="flex gap-3">
      <div
        className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
          done ? 'bg-green-100 text-green-700' : 'bg-brand-bg-soft text-brand-ink-2'
        }`}
      >
        {done ? <CheckCircle2 className="h-3.5 w-3.5" /> : index}
      </div>
      <div className="min-w-0 flex-1">
        <h4 className="font-sans text-sm font-bold text-brand-ink">{title}</h4>
        {hint && <p className="mt-0.5 font-sans text-xs text-brand-ink-2">{hint}</p>}
        <div className="mt-2">{children}</div>
      </div>
    </div>
  )
}

function CopyField({ label, value }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div>
      <div className="mb-1 font-sans text-[11px] font-bold uppercase tracking-wide text-brand-ink-2">
        {label}
      </div>
      <div className="flex items-stretch gap-2">
        <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded-lg bg-brand-bg px-3 py-2 font-mono text-xs text-brand-ink">
          {value}
        </code>
        <button
          type="button"
          onClick={copy}
          className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-brand-line px-3 py-2 font-sans text-xs font-medium text-brand-ink transition-colors hover:bg-brand-bg-soft"
        >
          <Copy className="h-3 w-3" />
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
    </div>
  )
}

export default function TeamsVoiceTab({ status, onStatus, showFlash }) {
  const [directory, setDirectory] = useState(status?.entra_tenant_id || '')
  const [busy, setBusy] = useState(null)
  const [probe, setProbe] = useState(null)

  const run = async (key, fn, successText) => {
    setBusy(key)
    try {
      const result = await fn()
      if (successText) showFlash(successText)
      return result
    } catch (err) {
      showFlash(errorText(err, 'The request failed.'), 'error')
      return null
    } finally {
      setBusy(null)
    }
  }

  const saveDirectory = async () => {
    const next = await run(
      'directory',
      () => updateTeamsVoiceSettings({ entra_tenant_id: directory.trim() }),
      'Directory saved.',
    )
    if (next) onStatus(next)
  }

  const toggleEnabled = async (enabled) => {
    const next = await run(
      'enable',
      () => updateTeamsVoiceSettings({ is_enabled: enabled }),
      enabled ? 'Voice capture enabled.' : 'Voice capture disabled.',
    )
    if (next) onStatus(next)
  }

  const connectSubscription = async () => {
    const next = await run(
      'subscribe',
      createTeamsVoiceSubscription,
      'Microsoft is now notifying LawHand of new calls.',
    )
    if (next) onStatus(next)
  }

  const removeSubscription = async () => {
    const next = await run(
      'unsubscribe',
      deleteTeamsVoiceSubscription,
      'Live call notifications stopped.',
    )
    if (next) onStatus(next)
  }

  const testConnection = async () => {
    const result = await run('test', testTeamsVoiceConnection)
    if (result) {
      setProbe(result)
      showFlash(
        `Connection works — ${result.sample_count} call(s) in the last 24 hours, ` +
          `${result.inbound_count} inbound.`,
      )
    }
  }

  const syncNow = async () => {
    const result = await run('sync', () => syncTeamsVoiceCalls(7))
    if (result) {
      showFlash(
        `Reconciled 7 days: ${result.imported} imported, ${result.updated} updated, ` +
          `${result.skipped} unchanged.`,
      )
    }
  }

  const configured = Boolean(status?.configured)
  const enabled = Boolean(status?.enabled)
  const subscribed = Boolean(status?.subscription_active)

  return (
    <div className="space-y-6">
      {/* What this actually does, in the customer's words. */}
      <div className="rounded-xl border border-brand-line bg-brand-surface p-6">
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-ink text-white">
            <PhoneCall className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <h3 className="font-sans text-base font-bold text-brand-ink">
              Teams Phone call capture
            </h3>
            <p className="mt-1 font-sans text-xs leading-5 text-brand-ink-2">
              Inbound calls to your firm&apos;s Teams Phone numbers land in the intake
              dashboard beside Zoom Phone calls — same feed, same follow-up tasks.
              Outbound and internal Teams calls are ignored.
            </p>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Calls captured" value={status?.captured_call_count ?? 0} />
          <Stat
            label="Most recent"
            value={when(status?.last_call_at) || 'None yet'}
            small
          />
          <Stat
            label="Live notifications"
            value={subscribed ? 'On' : 'Off'}
            tone={subscribed ? 'good' : 'idle'}
          />
          <Stat
            label="Renews"
            value={when(status?.subscription_expires_at) || '—'}
            small
          />
        </div>

        {status?.last_sync_error && (
          <div className="mt-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 font-sans text-xs text-amber-800">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              <span className="font-bold">Last background run failed:</span>{' '}
              {status.last_sync_error}
            </span>
          </div>
        )}
      </div>

      {/* Setup */}
      <div className="space-y-6 rounded-xl border border-brand-line bg-brand-surface p-6">
        <h3 className="font-sans text-base font-bold text-brand-ink">Setup</h3>

        <Step
          index={1}
          title="Name your Microsoft Entra directory"
          done={configured}
          hint="Entra admin center → Overview → Tenant ID. Call records are read with an application permission, which cannot use the shared “common” endpoint."
        >
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_auto]">
            <input
              value={directory}
              onChange={(e) => setDirectory(e.target.value)}
              placeholder="00000000-0000-0000-0000-000000000000"
              className="rounded-lg border border-brand-line bg-brand-bg px-3 py-2 font-mono text-xs text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
            />
            <button
              type="button"
              onClick={saveDirectory}
              disabled={busy === 'directory' || !directory.trim()}
              className="rounded-lg bg-brand-ink px-4 py-2 font-sans text-xs font-medium text-white transition-colors hover:bg-brand-ink/90 disabled:opacity-50"
            >
              {busy === 'directory' ? 'Saving…' : 'Save directory'}
            </button>
          </div>
        </Step>

        <Step
          index={2}
          title="Grant the application permission"
          done={configured && subscribed}
          hint={`A Microsoft 365 global admin consents once to ${
            status?.required_application_permission || 'CallRecords.Read.All'
          } — the only permission voice capture uses.`}
        >
          {status?.admin_consent_url ? (
            <a
              href={status.admin_consent_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-lg border border-brand-line px-4 py-2 font-sans text-xs font-medium text-brand-ink transition-colors hover:bg-brand-bg-soft"
            >
              <ShieldCheck className="h-3.5 w-3.5" />
              Open the Microsoft consent screen
              <ExternalLink className="h-3 w-3" />
            </a>
          ) : (
            <p className="font-sans text-xs text-brand-ink-2">
              Save your directory ID first to get a consent link.
            </p>
          )}
        </Step>

        <Step
          index={3}
          title="Turn on capture and start live notifications"
          done={enabled && subscribed}
          hint="Microsoft validates the notification URL below before it starts sending. The hourly usage-report sweep keeps working either way — it is just slower."
        >
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => toggleEnabled(!enabled)}
                disabled={busy === 'enable' || (!configured && !enabled)}
                className={`rounded-lg px-4 py-2 font-sans text-xs font-medium transition-colors disabled:opacity-50 ${
                  enabled
                    ? 'border border-brand-line text-brand-ink hover:bg-brand-bg-soft'
                    : 'bg-brand-ink text-white hover:bg-brand-ink/90'
                }`}
              >
                {busy === 'enable'
                  ? 'Working…'
                  : enabled
                    ? 'Disable voice capture'
                    : 'Enable voice capture'}
              </button>
              <button
                type="button"
                onClick={subscribed ? removeSubscription : connectSubscription}
                disabled={!enabled || busy === 'subscribe' || busy === 'unsubscribe'}
                className="rounded-lg border border-brand-line px-4 py-2 font-sans text-xs font-medium text-brand-ink transition-colors hover:bg-brand-bg-soft disabled:opacity-50"
              >
                {busy === 'subscribe' || busy === 'unsubscribe'
                  ? 'Working…'
                  : subscribed
                    ? 'Stop live notifications'
                    : 'Start live notifications'}
              </button>
            </div>
            <CopyField label="Notification URL" value={status?.webhook_url || ''} />
          </div>
        </Step>
      </div>

      {/* Verify */}
      <div className="rounded-xl border border-brand-line bg-brand-surface p-6">
        <h3 className="font-sans text-base font-bold text-brand-ink">Verify</h3>
        <p className="mt-1 font-sans text-xs text-brand-ink-2">
          The test reads your last 24 hours of Teams Phone usage — it proves the
          credential and the permission without waiting for a call.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={testConnection}
            disabled={!enabled || busy === 'test'}
            className="inline-flex items-center gap-1.5 rounded-lg border border-brand-line px-4 py-2 font-sans text-xs font-medium text-brand-ink transition-colors hover:bg-brand-bg-soft disabled:opacity-50"
          >
            <ShieldCheck className="h-3.5 w-3.5" />
            {busy === 'test' ? 'Testing…' : 'Test connection'}
          </button>
          <button
            type="button"
            onClick={syncNow}
            disabled={!enabled || busy === 'sync'}
            className="inline-flex items-center gap-1.5 rounded-lg border border-brand-line px-4 py-2 font-sans text-xs font-medium text-brand-ink transition-colors hover:bg-brand-bg-soft disabled:opacity-50"
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${busy === 'sync' ? 'animate-spin' : ''}`}
            />
            {busy === 'sync' ? 'Importing…' : 'Import last 7 days'}
          </button>
          {!enabled && (
            <span className="font-sans text-xs text-brand-ink-2">
              Enable voice capture to run these.
            </span>
          )}
        </div>
        {probe && (
          <div className="mt-3 inline-flex items-center gap-2 rounded-lg bg-green-50 px-3 py-2 font-sans text-xs font-medium text-green-700">
            <CheckCircle2 className="h-3.5 w-3.5" />
            {probe.sample_count} call(s) in the last 24 hours · {probe.inbound_count}{' '}
            inbound
          </div>
        )}
        {status?.app_credentials_source === 'platform' && (
          <p className="mt-4 font-sans text-[11px] leading-5 text-brand-ink-2">
            Using the LawHand Microsoft application. Firms that prefer to own the
            registration can register a single-tenant Entra app with only{' '}
            <code className="font-mono">
              {status?.required_application_permission || 'CallRecords.Read.All'}
            </code>{' '}
            and supply its credentials.
          </p>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value, tone, small }) {
  const toneClass =
    tone === 'good'
      ? 'text-green-700'
      : tone === 'idle'
        ? 'text-brand-ink-2'
        : 'text-brand-ink'
  return (
    <div className="rounded-lg bg-brand-bg px-3 py-2">
      <div className="font-sans text-[10px] font-bold uppercase tracking-wide text-brand-ink-2">
        {label}
      </div>
      <div
        className={`mt-0.5 font-sans font-bold ${small ? 'text-xs' : 'text-lg'} ${toneClass}`}
      >
        {value}
      </div>
    </div>
  )
}
