import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  AlertCircle,
  ArrowLeft,
  FileDiff,
  FileText,
  History,
  Loader2,
  MessageSquareText,
} from 'lucide-react'
import {
  approveMatterDocumentRevision,
  createMatterDocumentRevision,
  getMatterDocumentRevisionArtifactUrl,
  getMatterDocuments,
  listMatterDocumentRevisions,
  listSignatureRequests,
  prepareMatterDocumentRevisionESignReplacement,
  rejectMatterDocumentRevision,
} from '../api'
import RevisionComposer from '../components/documentRevision/RevisionComposer'
import RevisionChanges from '../components/documentRevision/RevisionChanges'
import RenderedRevisionPreview from '../components/documentRevision/RenderedRevisionPreview'
import SignatureReplacementReview from '../components/documentRevision/SignatureReplacementReview'
import useDocumentRevision from '../hooks/useDocumentRevision'

const TERMINAL_REVIEW_STATUSES = new Set(['ready_for_review', 'approved', 'rejected', 'superseded'])

const makeRequestId = () => {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  const bytes = new Uint8Array(16)
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256)
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0'))
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`
}

const revisionIdOf = (value) => value?.id || value?.revision_id || value?.revision?.id || ''
const unwrapRevision = (value) => value?.revision || value

const apiErrorMessage = (error, fallback) => {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (typeof detail?.message === 'string') return detail.message
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => item?.msg || item?.message).filter(Boolean)
    if (messages.length) return messages.join(' ')
  }
  return error?.message || fallback
}

const documentFields = (revision, prefix) => {
  const nested = revision?.[`${prefix}_document`] || {}
  return {
    id: nested.id || revision?.[`${prefix}_document_id`] || '',
    filename: nested.filename || revision?.[`${prefix}_filename`] || revision?.[`${prefix}_document_filename`] || '',
    sha256: nested.sha256 || revision?.[`${prefix}_sha256`] || revision?.[`${prefix}_document_sha256`] || '',
  }
}

const statusCopy = (status) => ({
  processing: { label: 'Preparing revision', classes: 'border-blue-200 bg-blue-50 text-blue-700' },
  needs_input: { label: 'Needs input', classes: 'border-amber-200 bg-amber-50 text-amber-800' },
  ready_for_review: { label: 'Ready for review', classes: 'border-brand-accent/25 bg-brand-accent/10 text-brand-accent-2' },
  approved: { label: 'Approved', classes: 'border-brand-green/25 bg-brand-green/10 text-brand-green' },
  rejected: { label: 'Rejected', classes: 'border-brand-rose/25 bg-brand-rose/10 text-brand-rose' },
  superseded: { label: 'Superseded', classes: 'border-amber-200 bg-amber-50 text-amber-800' },
  failed: { label: 'Failed', classes: 'border-brand-rose/25 bg-brand-rose/10 text-brand-rose' },
}[status] || { label: status || 'Not started', classes: 'border-brand-line bg-brand-bg-soft text-brand-muted' })

function RevisionStatus({ status }) {
  const copy = statusCopy(status)
  return <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${copy.classes}`}>{copy.label}</span>
}

function LoadingPanel({ label = 'Loading document revision' }) {
  return (
    <div className="flex min-h-56 items-center justify-center rounded-2xl border border-brand-line bg-brand-surface" role="status">
      <div className="text-center">
        <Loader2 size={24} className="mx-auto animate-spin text-brand-accent-2" aria-hidden="true" />
        <p className="mt-2 text-sm font-semibold text-brand-ink">{label}</p>
      </div>
    </div>
  )
}

export default function DocumentRevisionPage() {
  const { matterId, documentId, revisionId } = useParams()
  const navigate = useNavigate()
  const { revision, setRevision, loading: revisionLoading, error: revisionLoadError } = useDocumentRevision(matterId, revisionId)
  const [sourceDocument, setSourceDocument] = useState(null)
  const [priorRevisions, setPriorRevisions] = useState([])
  const [contextLoading, setContextLoading] = useState(true)
  const [stage, setStage] = useState(revisionId ? 'changes' : 'request')
  const [submitting, setSubmitting] = useState(false)
  const [approving, setApproving] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [reviewed, setReviewed] = useState(false)
  const [actionError, setActionError] = useState('')
  const [signatureRequests, setSignatureRequests] = useState([])
  const [signatureLoading, setSignatureLoading] = useState(false)
  const [preparingRequestId, setPreparingRequestId] = useState('')
  const [replacementPreview, setReplacementPreview] = useState(null)

  const sourceFromRevision = useMemo(() => documentFields(revision, 'source'), [revision])
  const outputDocument = useMemo(() => documentFields(revision, 'output'), [revision])
  const currentSource = sourceFromRevision.id
    ? sourceFromRevision
    : sourceDocument || { id: documentId, filename: 'Matter document', sha256: '' }
  const rootDocumentId = revision?.root_document_id || documentId
  const artifactUrl = revision?.artifact_url || (revisionId ? getMatterDocumentRevisionArtifactUrl(matterId, revisionId) : '#')

  useEffect(() => {
    let active = true
    setContextLoading(true)
    Promise.all([
      getMatterDocuments(matterId).catch(() => ({ items: [] })),
      listMatterDocumentRevisions(matterId, rootDocumentId).catch(() => ({ items: [] })),
    ]).then(([documentsResponse, revisionsResponse]) => {
      if (!active) return
      const documents = Array.isArray(documentsResponse) ? documentsResponse : documentsResponse?.items || []
      const revisions = Array.isArray(revisionsResponse) ? revisionsResponse : revisionsResponse?.items || revisionsResponse?.revisions || []
      setSourceDocument(documents.find((document) => String(document.id) === String(documentId)) || null)
      setPriorRevisions(revisions)
    }).finally(() => {
      if (active) setContextLoading(false)
    })
    return () => { active = false }
  }, [documentId, matterId, rootDocumentId])

  useEffect(() => {
    setReviewed(false)
    setReplacementPreview(revision?.prepared_esign_preview || null)
    if (revision?.status === 'ready_for_review') setStage('changes')
    if (revision?.status === 'approved') setStage('preview')
  }, [revision?.id, revision?.status, revision?.output_sha256, revision?.prepared_esign_preview])

  useEffect(() => {
    if (revision?.status !== 'approved') {
      setSignatureRequests([])
      return undefined
    }
    let active = true
    setSignatureLoading(true)
    listSignatureRequests(matterId)
      .then((response) => {
        if (active) setSignatureRequests(Array.isArray(response) ? response : response?.items || [])
      })
      .catch((error) => {
        if (active) setActionError(apiErrorMessage(error, 'Could not load matter signature requests.'))
      })
      .finally(() => {
        if (active) setSignatureLoading(false)
      })
    return () => { active = false }
  }, [matterId, revision?.status])

  const submitInstruction = async (instruction, modelTier) => {
    const sourceId = revisionId ? (outputDocument.id || sourceFromRevision.id || documentId) : documentId
    if (!sourceId) {
      setActionError('This revision does not identify a safe source document for another change.')
      return false
    }
    setSubmitting(true)
    setActionError('')
    try {
      const created = await createMatterDocumentRevision(matterId, sourceId, {
        instruction,
        client_request_id: makeRequestId(),
        model_tier: modelTier,
      })
      const nextRevisionId = revisionIdOf(created)
      if (!nextRevisionId) throw new Error('The server did not return a revision ID.')
      navigate(`/matters/${matterId}/documents/${sourceId}/revisions/${nextRevisionId}`)
      return true
    } catch (error) {
      setActionError(apiErrorMessage(error, 'The revision could not be prepared.'))
      return false
    } finally {
      setSubmitting(false)
    }
  }

  const approveRevision = async () => {
    const outputSha = outputDocument.sha256 || revision?.output_sha256 || ''
    if (!reviewed || !outputSha || !revisionId) return
    setApproving(true)
    setActionError('')
    try {
      const approved = await approveMatterDocumentRevision(matterId, revisionId, {
        reviewed_output_sha256: outputSha,
      })
      setRevision(unwrapRevision(approved))
      setReviewed(false)
    } catch (error) {
      setActionError(apiErrorMessage(error, 'The revision was not approved.'))
    } finally {
      setApproving(false)
    }
  }

  const rejectRevision = async () => {
    if (!revisionId) return
    setRejecting(true)
    setActionError('')
    try {
      const rejected = await rejectMatterDocumentRevision(matterId, revisionId)
      setRevision(unwrapRevision(rejected))
      setReviewed(false)
    } catch (error) {
      setActionError(apiErrorMessage(error, 'The revision was not rejected.'))
    } finally {
      setRejecting(false)
    }
  }

  const prepareReplacement = async (signatureRequestId) => {
    setPreparingRequestId(signatureRequestId)
    setReplacementPreview(null)
    setActionError('')
    try {
      const prepared = await prepareMatterDocumentRevisionESignReplacement(matterId, revisionId, {
        signature_request_id: signatureRequestId,
      })
      const preparedRevision = unwrapRevision(prepared)
      setReplacementPreview(preparedRevision?.prepared_esign_preview || preparedRevision)
      if (preparedRevision?.id) setRevision(preparedRevision)
    } catch (error) {
      setActionError(apiErrorMessage(error, 'The replacement preview could not be prepared.'))
    } finally {
      setPreparingRequestId('')
    }
  }

  const status = revision?.status || ''
  const reviewable = TERMINAL_REVIEW_STATUSES.has(status)
  const showRightPanel = Boolean(revisionId)
  const loadError = revisionLoadError || actionError

  return (
    <div className="min-h-full bg-brand-bg text-brand-ink">
      <header className="sticky top-0 z-20 border-b border-brand-line bg-brand-surface/95 px-4 py-3 backdrop-blur sm:px-6">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <Link to={`/matters/${matterId}`} className="tap-target -ml-2 flex shrink-0 items-center justify-center rounded-xl text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink" aria-label="Back to matter">
              <ArrowLeft size={19} aria-hidden="true" />
            </Link>
            <div className="min-w-0">
              <p className="text-[9px] font-bold uppercase tracking-[0.14em] text-brand-muted">Document revision</p>
              <h1 className="truncate text-base font-bold text-brand-ink sm:text-lg">{currentSource.filename || 'Matter document'}</h1>
            </div>
          </div>
          <RevisionStatus status={status} />
        </div>
      </header>

      <nav className="sticky top-[61px] z-10 grid grid-cols-3 border-b border-brand-line bg-brand-surface lg:hidden" aria-label="Revision stages">
        {[
          { value: 'request', label: 'Request', Icon: MessageSquareText },
          { value: 'changes', label: 'Changes', Icon: FileDiff },
          { value: 'preview', label: 'Preview', Icon: FileText },
        ].map(({ value, label, Icon }) => (
          <button
            key={value}
            type="button"
            onClick={() => setStage(value)}
            aria-current={stage === value ? 'step' : undefined}
            disabled={value !== 'request' && !showRightPanel}
            className={`flex min-h-12 items-center justify-center gap-1.5 border-b-2 text-xs font-bold disabled:opacity-40 ${stage === value ? 'border-brand-ink text-brand-ink' : 'border-transparent text-brand-muted'}`}
          >
            <Icon size={15} aria-hidden="true" /> {label}
          </button>
        ))}
      </nav>

      {loadError && (
        <div className="mx-auto mt-4 flex max-w-7xl items-start gap-2.5 rounded-xl border border-brand-rose/30 bg-brand-rose/5 p-4 text-sm text-brand-rose sm:mx-6 lg:mx-auto" role="alert">
          <AlertCircle size={18} className="mt-0.5 shrink-0" aria-hidden="true" /><span>{loadError}</span>
        </div>
      )}

      <div className="mx-auto grid max-w-7xl gap-5 px-3 py-4 sm:px-6 sm:py-6 lg:grid-cols-[minmax(300px,380px)_minmax(0,1fr)]">
        <aside className={`${stage === 'request' ? 'block' : 'hidden'} space-y-4 lg:block`} aria-label="Revision request">
          <section className="rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-bg-soft text-brand-muted"><FileText size={17} aria-hidden="true" /></span>
              <div className="min-w-0">
                <p className="text-[10px] font-bold uppercase tracking-wide text-brand-muted">Source document</p>
                <p className="mt-1 truncate text-sm font-bold text-brand-ink">{currentSource.filename || 'Matter document'}</p>
                <p className="mt-1 text-[11px] leading-relaxed text-brand-muted">The source remains unchanged. Every request creates a new private DOCX revision.</p>
              </div>
            </div>
          </section>

          {revision?.instruction && (
            <section className="rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm" aria-labelledby="current-request-title">
              <p id="current-request-title" className="text-[10px] font-bold uppercase tracking-wide text-brand-muted">Current request</p>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-brand-ink">{revision.instruction}</p>
              {revision.requested_model_tier && <p className="mt-2 text-[10px] font-bold uppercase tracking-wide text-brand-accent-2">{revision.requested_model_tier} model requested</p>}
            </section>
          )}

          {status === 'processing' && <LoadingPanel label="Preparing a private revision" />}
          {status === 'needs_input' && (
            <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950" role="status">
              <p className="font-bold">The assistant needs another detail.</p>
              <p className="mt-1 leading-relaxed">{revision.clarification_question || 'Add a more specific instruction below.'}</p>
            </div>
          )}
          {status === 'failed' && (
            <div className="rounded-2xl border border-brand-rose/30 bg-brand-rose/5 p-4 text-sm text-brand-rose" role="alert">
              <p className="font-bold">Revision failed</p>
              <p className="mt-1 leading-relaxed">{revision.error_message || 'The source document was not changed. Try a narrower request.'}</p>
            </div>
          )}

          {!revisionId && priorRevisions.length > 0 && (
            <section className="rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm" aria-labelledby="prior-revisions-title">
              <div className="flex items-center gap-2"><History size={15} className="text-brand-muted" aria-hidden="true" /><h2 id="prior-revisions-title" className="text-xs font-bold uppercase tracking-wide text-brand-muted">Recent revisions</h2></div>
              <div className="mt-3 space-y-2">
                {priorRevisions.slice(0, 5).map((item) => (
                  <Link key={revisionIdOf(item)} to={`/matters/${matterId}/documents/${rootDocumentId}/revisions/${revisionIdOf(item)}`} className="block rounded-xl border border-brand-line px-3 py-2.5 hover:bg-brand-bg-soft">
                    <div className="flex items-center justify-between gap-2"><span className="truncate text-xs font-bold text-brand-ink">{item.summary || item.instruction || `Revision ${item.version_no || ''}`}</span><RevisionStatus status={item.status} /></div>
                  </Link>
                ))}
              </div>
            </section>
          )}

          {status !== 'processing' && (
            <RevisionComposer
              onSubmit={submitInstruction}
              submitting={submitting}
              followUp={Boolean(revisionId)}
            />
          )}
        </aside>

        <main className={`${stage === 'request' ? 'hidden' : 'block'} min-w-0 lg:block`} aria-label="Revision review">
          <div className="mb-4 hidden items-center justify-between gap-3 lg:flex">
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-muted">Review workspace</p>
              <h2 className="mt-1 font-serif text-2xl font-bold text-brand-ink">{stage === 'preview' ? 'Artifact preview and approval' : 'Before-and-after changes'}</h2>
            </div>
            <div className="inline-flex rounded-xl border border-brand-line bg-brand-surface p-1">
              <button type="button" onClick={() => setStage('changes')} disabled={!showRightPanel} className={`min-h-9 rounded-lg px-3 text-xs font-bold ${stage !== 'preview' ? 'bg-brand-bg-soft text-brand-ink' : 'text-brand-muted'}`}>Changes</button>
              <button type="button" onClick={() => setStage('preview')} disabled={!showRightPanel} className={`min-h-9 rounded-lg px-3 text-xs font-bold ${stage === 'preview' ? 'bg-brand-bg-soft text-brand-ink' : 'text-brand-muted'}`}>Preview</button>
            </div>
          </div>

          {!revisionId ? (
            <div className="rounded-2xl border border-dashed border-brand-line-2 bg-brand-surface p-10 text-center">
              <MessageSquareText size={28} className="mx-auto text-brand-muted" aria-hidden="true" />
              <p className="mt-3 font-serif text-xl font-bold text-brand-ink">Describe the first change</p>
              <p className="mx-auto mt-1 max-w-md text-sm leading-relaxed text-brand-muted">The assistant will create a separate private DOCX and return exact before-and-after evidence.</p>
            </div>
          ) : revisionLoading && !revision ? (
            <LoadingPanel />
          ) : status === 'processing' ? (
            <LoadingPanel label="Applying bounded DOCX changes" />
          ) : reviewable ? (
            stage === 'preview' ? (
              <div className="space-y-5">
                <RenderedRevisionPreview
                  revision={revision}
                  artifactUrl={artifactUrl}
                  reviewed={reviewed}
                  onReviewedChange={setReviewed}
                  onApprove={approveRevision}
                  onReject={rejectRevision}
                  approving={approving}
                  rejecting={rejecting}
                />
                {status === 'approved' && (
                  signatureLoading ? <LoadingPanel label="Loading signature requests" /> : (
                    <SignatureReplacementReview
                      requests={signatureRequests}
                      preview={replacementPreview || revision.prepared_esign_preview}
                      preparingRequestId={preparingRequestId}
                      onPrepare={prepareReplacement}
                    />
                  )
                )}
              </div>
            ) : (
              <RevisionChanges revision={revision} />
            )
          ) : status === 'needs_input' ? (
            <div className="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-center text-amber-950"><p className="font-bold">Answer the clarification in Request.</p></div>
          ) : status === 'failed' ? (
            <div className="rounded-2xl border border-brand-rose/30 bg-brand-rose/5 p-6 text-center text-brand-rose"><AlertCircle size={24} className="mx-auto" /><p className="mt-2 font-bold">No revision artifact was created.</p></div>
          ) : null}
        </main>
      </div>

      {contextLoading && !revisionId && (
        <div className="fixed bottom-24 left-1/2 z-30 -translate-x-1/2 rounded-full border border-brand-line bg-brand-surface px-3 py-1.5 text-xs text-brand-muted shadow-sm" role="status">Loading matter documents…</div>
      )}
    </div>
  )
}
