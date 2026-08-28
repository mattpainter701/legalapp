import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  acceptComplianceAgreement,
  executeRetention,
  getComplianceAgreements,
  getRetentionInventory,
  updateRetentionPolicy,
} from '../api'
import { useConfirm } from './dialog/ConfirmProvider'

const ATTESTATION = 'I confirm that I am authorized to bind this organization to the identified agreement.'

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}

function readableName(value) {
  return String(value || '').replaceAll('_', ' ')
}

export function AgreementAcceptancePanel({ compact = false, onStatusChange }) {
  const [status, setStatus] = useState(null)
  const [signerName, setSignerName] = useState('')
  const [signerTitle, setSignerTitle] = useState('')
  const [authority, setAuthority] = useState(false)
  const [submitting, setSubmitting] = useState('')
  const [error, setError] = useState('')

  const updateStatus = useCallback((next) => {
    setStatus(next)
    onStatusChange?.(next)
  }, [onStatusChange])

  useEffect(() => {
    let active = true
    getComplianceAgreements()
      .then((next) => { if (active) updateStatus(next) })
      .catch(() => { if (active) setError('Unable to load tenant agreement status.') })
    return () => { active = false }
  }, [updateStatus])

  const outstanding = useMemo(
    () => status?.agreements?.filter((agreement) => agreement.required && !agreement.accepted) || [],
    [status],
  )

  const accept = async (agreement) => {
    setError('')
    setSubmitting(agreement.kind)
    try {
      const next = await acceptComplianceAgreement(agreement.kind, {
        expected_version: agreement.version,
        expected_content_hash: agreement.content_hash,
        signer_name: signerName,
        signer_title: signerTitle,
        authority_attested: authority,
      })
      updateStatus(next)
      setAuthority(false)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Unable to record agreement acceptance.')
    } finally {
      setSubmitting('')
    }
  }

  if (error && !status) return <div role="alert" className="rounded-lg border border-brand-rose/30 bg-brand-rose/5 p-3 text-sm text-brand-rose">{error}</div>
  if (!status) return <div aria-busy="true" className="text-sm text-brand-muted">Loading tenant agreements…</div>

  return (
    <section aria-label="Tenant agreements" className={compact ? 'space-y-4' : 'rounded-xl border border-brand-line bg-brand-surface p-5 space-y-5'}>
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="font-serif text-lg font-bold text-brand-ink">Tenant agreements</h3>
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${status.complete ? 'bg-green-100 text-green-800' : status.configured ? 'bg-brand-amber/10 text-brand-amber' : 'bg-brand-bg text-brand-muted'}`}>
            {status.complete ? 'Current' : status.configured ? 'Action required' : 'Not published'}
          </span>
        </div>
        <p className="mt-1 text-sm leading-6 text-brand-muted">
          A tenant administrator must review each counsel-approved document and attest authority to bind the organization.
          {status.enforced ? ' Acceptance is required before connecting cloud storage.' : ' Enforcement is currently in rollout mode.'}
        </p>
      </div>

      {!status.configured && (
        <p className="rounded-lg border border-brand-line bg-brand-bg px-3 py-2 text-sm text-brand-muted">
          No required agreement versions have been published by the platform operator yet.
        </p>
      )}

      <div className="space-y-3">
        {status.agreements.map((agreement) => (
          <article key={agreement.id} className="rounded-lg border border-brand-line bg-brand-bg-soft p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <a href={agreement.document_url} target="_blank" rel="noreferrer" className="font-medium text-brand-accent hover:underline">
                  {agreement.title}
                </a>
                <p className="mt-1 text-xs text-brand-muted">Version {agreement.version} · SHA-256 {agreement.content_hash.slice(0, 12)}…</p>
              </div>
              <span className={`text-xs font-semibold ${agreement.accepted ? 'text-green-700' : 'text-brand-amber'}`}>
                {agreement.accepted ? 'Accepted' : 'Awaiting acceptance'}
              </span>
            </div>
            {agreement.accepted && (
              <p className="mt-3 text-xs text-brand-muted">
                Accepted by {agreement.signer_name}{agreement.signer_title ? `, ${agreement.signer_title}` : ''} on {new Date(agreement.accepted_at).toLocaleString()}.
              </p>
            )}
          </article>
        ))}
      </div>

      {outstanding.length > 0 && (
        <div className="space-y-3 rounded-lg border border-brand-accent/20 bg-brand-accent/5 p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm font-medium text-brand-ink">
              Full legal name
              <input value={signerName} onChange={(event) => setSignerName(event.target.value)} maxLength={255} className="mt-1 w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2 font-normal" />
            </label>
            <label className="text-sm font-medium text-brand-ink">
              Title / authority
              <input value={signerTitle} onChange={(event) => setSignerTitle(event.target.value)} maxLength={255} className="mt-1 w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2 font-normal" />
            </label>
          </div>
          <label className="flex items-start gap-3 text-sm leading-5 text-brand-ink">
            <input type="checkbox" checked={authority} onChange={(event) => setAuthority(event.target.checked)} className="mt-1" />
            <span>{ATTESTATION}</span>
          </label>
          <div className="flex flex-wrap gap-2">
            {outstanding.map((agreement) => (
              <button
                key={agreement.id}
                type="button"
                disabled={!signerName.trim() || !signerTitle.trim() || !authority || Boolean(submitting)}
                onClick={() => accept(agreement)}
                className="rounded-lg bg-brand-ink px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                {submitting === agreement.kind ? 'Recording…' : `Accept ${agreement.title}`}
              </button>
            ))}
          </div>
        </div>
      )}
      {error && <p role="alert" className="text-sm text-brand-rose">{error}</p>}
    </section>
  )
}

export default function CompliancePanel() {
  const confirmAction = useConfirm()
  const [retention, setRetention] = useState(null)
  const [days, setDays] = useState(7)
  const [legalHold, setLegalHold] = useState(false)
  const [legalHoldReason, setLegalHoldReason] = useState('')
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const applyRetention = useCallback((next) => {
    setRetention(next)
    setDays(next.policy?.chat_attachments_days || 7)
    setLegalHold(Boolean(next.legal_hold))
    setLegalHoldReason(next.legal_hold_reason || '')
  }, [])

  useEffect(() => {
    getRetentionInventory().then(applyRetention).catch(() => setError('Unable to load the retention inventory.'))
  }, [applyRetention])

  const savePolicy = async () => {
    setBusy('save')
    setError('')
    try {
      applyRetention(await updateRetentionPolicy({
        chat_attachments_days: Number(days),
        legal_hold: legalHold,
        legal_hold_reason: legalHold ? legalHoldReason : null,
      }))
    } catch (err) {
      setError(err?.response?.data?.detail || 'Unable to update the retention policy.')
    } finally {
      setBusy('')
    }
  }

  const runPreview = async () => {
    setBusy('preview')
    setError('')
    try { setPreview(await executeRetention(true)) } catch { setError('Unable to preview retention cleanup.') } finally { setBusy('') }
  }

  const runCleanup = async () => {
    const approved = await confirmAction({
      title: 'Delete expired chat attachments?',
      message: 'This removes only the records and local files shown as eligible in the preview. Matter-linked documents are excluded. This action cannot be undone.',
      destructive: true,
      confirmLabel: 'Delete expired attachments',
    })
    if (!approved) return
    setBusy('execute')
    setError('')
    try {
      const result = await executeRetention(false)
      setPreview(result)
      applyRetention(await getRetentionInventory())
    } catch (err) {
      setError(err?.response?.status === 423 ? 'Cleanup is blocked by the active legal hold.' : 'Unable to execute retention cleanup.')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="space-y-6">
      <AgreementAcceptancePanel />
      <section aria-label="Data retention inventory" className="rounded-xl border border-brand-line bg-brand-surface p-5 space-y-5">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-serif text-lg font-bold text-brand-ink">Data inventory and retention</h3>
            {retention && <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${retention.legal_hold ? 'bg-brand-rose/10 text-brand-rose' : 'bg-green-100 text-green-800'}`}>{retention.legal_hold ? 'Legal hold active' : 'Normal retention'}</span>}
          </div>
          <p className="mt-1 text-sm leading-6 text-brand-muted">Metadata-only visibility across app data stores. Automated deletion is limited to expired, non-matter chat attachments.</p>
        </div>

        {!retention ? <div aria-busy="true" className="text-sm text-brand-muted">Loading retention inventory…</div> : (
          <>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {retention.categories.map((category) => (
                <div key={category.name} className="rounded-lg border border-brand-line bg-brand-bg-soft p-3">
                  <p className="text-sm font-medium capitalize text-brand-ink">{readableName(category.name)}</p>
                  <p className="mt-1 text-2xl font-semibold text-brand-ink">{category.record_count.toLocaleString()}</p>
                  <p className="text-xs text-brand-muted">{category.bytes == null ? category.system : `${formatBytes(category.bytes)} · ${category.system}`}</p>
                  <p className="mt-2 text-xs text-brand-muted">{readableName(category.retention_mode)}</p>
                </div>
              ))}
            </div>

            <div className="grid gap-4 rounded-lg border border-brand-line p-4 md:grid-cols-2">
              <label className="text-sm font-medium text-brand-ink">
                Non-matter chat attachment retention (days)
                <input type="number" min="1" max="365" value={days} onChange={(event) => setDays(event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2 font-normal" />
              </label>
              <div className="space-y-2">
                <label className="flex items-center gap-2 text-sm font-medium text-brand-ink">
                  <input type="checkbox" checked={legalHold} onChange={(event) => setLegalHold(event.target.checked)} />
                  Place all automated tenant cleanup on legal hold
                </label>
                {legalHold && <textarea value={legalHoldReason} onChange={(event) => setLegalHoldReason(event.target.value)} maxLength={2000} placeholder="Required reason / matter reference" className="w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2 text-sm" />}
              </div>
              <div className="md:col-span-2 flex flex-wrap gap-2">
                <button type="button" disabled={busy === 'save' || days < 1 || days > 365 || (legalHold && !legalHoldReason.trim())} onClick={savePolicy} className="rounded-lg bg-brand-ink px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{busy === 'save' ? 'Saving…' : 'Save policy'}</button>
                <button type="button" disabled={Boolean(busy)} onClick={runPreview} className="rounded-lg border border-brand-line px-4 py-2 text-sm font-medium text-brand-ink disabled:opacity-50">{busy === 'preview' ? 'Previewing…' : 'Preview cleanup'}</button>
                <button type="button" disabled={Boolean(busy) || legalHold || !preview} onClick={runCleanup} className="rounded-lg border border-brand-rose/30 px-4 py-2 text-sm font-medium text-brand-rose disabled:opacity-40">{busy === 'execute' ? 'Deleting…' : 'Delete previewed attachments'}</button>
              </div>
            </div>

            {preview && <p className="rounded-lg bg-brand-bg px-3 py-2 text-sm text-brand-muted">Preview/result: {preview.eligible_records} record(s), {formatBytes(preview.eligible_bytes)} eligible; {preview.deleted_records || 0} record(s) deleted.</p>}
          </>
        )}
        {error && <p role="alert" className="text-sm text-brand-rose">{error}</p>}
      </section>
    </div>
  )
}
