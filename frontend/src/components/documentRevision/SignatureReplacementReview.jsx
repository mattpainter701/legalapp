import React from 'react'
import { FileSignature, LockKeyhole, ShieldCheck } from 'lucide-react'

const signerIsPending = (signer) => signer?.status === 'pending'

const requestIsExpired = (request) => {
  if (!request?.expires_at) return false
  const expiresAt = new Date(request.expires_at).getTime()
  return Number.isFinite(expiresAt) && expiresAt <= Date.now()
}

const canPrepare = (request) => (
  ['draft', 'sent'].includes(request?.status)
  && (request?.provider || 'internal') === 'internal'
  && !requestIsExpired(request)
  && Array.isArray(request?.signers)
  && request.signers.length > 0
  && request.signers.every(signerIsPending)
)

export default function SignatureReplacementReview({
  requests = [],
  preview,
  preparingRequestId,
  onPrepare,
}) {
  const previewPayload = preview?.replacement_preview || preview || null
  const executable = previewPayload?.executable === true

  return (
    <section className="rounded-2xl border border-brand-line bg-brand-surface shadow-sm" aria-labelledby="signature-replacement-title">
      <header className="border-b border-brand-line bg-brand-bg-soft/50 px-4 py-4 sm:px-5">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-accent/10 text-brand-accent-2">
            <FileSignature size={19} aria-hidden="true" />
          </span>
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-muted">Separate action</p>
            <h2 id="signature-replacement-title" className="mt-1 font-serif text-xl font-bold text-brand-ink">Prepare portal signature replacement</h2>
            <p className="mt-1 text-xs leading-relaxed text-brand-muted">
              LawHand’s internal provider records a typed portal signature acknowledgment. It is not an external e-signature service and does not send an invitation.
            </p>
          </div>
        </div>
      </header>

      <div className="space-y-4 p-4 sm:p-5">
        {requests.length === 0 ? (
          <div className="rounded-xl border border-dashed border-brand-line-2 p-5 text-center">
            <p className="text-sm font-semibold text-brand-ink">No matter signature requests found.</p>
            <p className="mt-1 text-xs text-brand-muted">The approved document remains available in matter storage.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {requests.map((request) => {
              const eligible = canPrepare(request)
              return (
                <article key={request.id} className="rounded-xl border border-brand-line bg-white p-4">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="truncate text-sm font-bold text-brand-ink">{request.document_name || 'Matter document'}</h3>
                        <span className="rounded-full border border-brand-line bg-brand-bg-soft px-2 py-0.5 text-[10px] font-bold uppercase text-brand-muted">{String(request.status || 'unknown').replaceAll('_', ' ')}</span>
                      </div>
                      <p className="mt-1 text-xs text-brand-muted">
                        {(request.signers || []).map((signer) => `${signer.name} · ${signer.email}`).join(', ') || 'No signers returned'}
                      </p>
                      {!eligible && (
                        <p className="mt-2 text-[11px] leading-relaxed text-brand-rose">
                          Only an open, unexpired internal request with every signer still pending can be prepared for replacement.
                        </p>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => onPrepare(request.id)}
                      disabled={!eligible || Boolean(preparingRequestId)}
                      className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-xl border border-brand-line px-3.5 text-xs font-bold text-brand-ink hover:border-brand-accent hover:bg-brand-bg-soft disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {preparingRequestId === request.id ? <span className="h-4 w-4 animate-spin rounded-full border-2 border-brand-ink border-t-transparent" aria-hidden="true" /> : <ShieldCheck size={15} aria-hidden="true" />}
                      {preparingRequestId === request.id ? 'Preparing…' : 'Prepare replacement preview'}
                    </button>
                  </div>
                </article>
              )
            })}
          </div>
        )}

        {previewPayload && (
          <div className="rounded-2xl border border-brand-accent/25 bg-brand-accent/5 p-4" role="status" aria-live="polite">
            <div className="flex items-start gap-3">
              <LockKeyhole size={19} className="mt-0.5 shrink-0 text-brand-accent-2" aria-hidden="true" />
              <div>
                <p className="text-sm font-bold text-brand-ink">Replacement preview prepared</p>
                <p className="mt-1 text-xs leading-relaxed text-brand-ink-2">
                  Executable: {String(executable)}. This preview did not void the existing request, create a replacement, send an invitation, or notify a signer.
                </p>
                <p className="mt-2 text-[11px] font-bold uppercase tracking-wide text-brand-accent-2">Nothing was voided or sent</p>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
