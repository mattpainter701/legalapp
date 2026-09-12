import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CheckCircle2, Clock, Download, FileText, Upload } from 'lucide-react'
import api, {
  downloadClientPortalDocumentUrl,
  getClientPortalSignatureFields,
  signClientPortalSignature,
  uploadClientPortalSignedCopy,
} from '../api'
import { PdfPageCanvas, useTemplatePdfDocument } from './templates/PdfDocumentCanvas'
import { clamp, overlayToCanvasRect } from './templates/pdfFieldGeometry'
import {
  CONSENT_TEXT,
  CONSENT_TEXT_VERSION,
  SIGNED_COPY_RECEIVED_MESSAGE,
  signedOutcomeMessage,
} from './portal/signingMessages'

// Fields the client types into and that travel to the server as `field_values`.
// Signature, initials and date fields are stamped server-side from the adopted
// name and signing time, so they never appear in that map.
const FILLABLE_KINDS = new Set(['text', 'checkbox', 'radio', 'choice'])
const SIGNING_KINDS = new Set(['signature', 'initials'])

const KIND_LABELS = {
  text: 'Text',
  checkbox: 'Checkbox',
  radio: 'Choice',
  choice: 'Choice',
  signature: 'Signature',
  initials: 'Initials',
  date: 'Date',
}

const INPUT_CLASS = 'w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40'
const PRIMARY_BUTTON = 'px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-lg hover:bg-brand-ink-2 transition-all disabled:opacity-50 disabled:cursor-not-allowed'
// The adopted signature mirrors the italic serif the server stamps onto the
// executed PDF, so what the client sees is what ends up on the document.
const SIGNATURE_FONT = { fontFamily: 'Georgia, "Times New Roman", Times, serif' }

export const initialsFor = (name) => String(name || '')
  .trim()
  .split(/\s+/)
  .filter(Boolean)
  .map((part) => part[0].toUpperCase())
  .join('')

const fieldLabel = (field) => field.label || KIND_LABELS[field.kind] || 'Field'

const optionsFor = (field) => (Array.isArray(field.options) ? field.options : []).map((option) => (
  typeof option === 'string'
    ? { value: option, label: option }
    : { value: String(option?.value ?? option?.label ?? ''), label: String(option?.label ?? option?.value ?? '') }
))

function initialValues(fields) {
  const values = {}
  for (const field of fields) {
    if (field.mine === false || !FILLABLE_KINDS.has(field.kind)) continue
    values[field.field_id] = field.kind === 'checkbox'
      ? (String(field.value).toLowerCase() === 'true' ? 'true' : 'false')
      : String(field.value ?? '')
  }
  return values
}

function errorMessage(err, fallback) {
  const detail = err?.response?.data?.detail
  return typeof detail === 'string' ? detail : fallback
}

function todayLabel() {
  return new Date().toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

function FieldOverlay({ field, rect, value, adopted, typedName, onChange, onAdopt }) {
  const label = fieldLabel(field)
  const style = {
    left: rect.x,
    top: rect.y,
    width: rect.width,
    height: rect.height,
    fontSize: clamp(rect.height * (field.multiline ? 0.28 : 0.55), 9, 16),
  }
  // `touch-pan-y` keeps a finger drag over an input scrolling the document
  // instead of getting stuck on the control.
  const base = 'absolute touch-pan-y font-sans text-brand-ink'

  if (field.mine === false) {
    return (
      <input
        type="text"
        readOnly
        tabIndex={-1}
        aria-label={`${label} (completed by another signer)`}
        value={field.value ?? ''}
        style={style}
        className={`${base} truncate rounded border border-brand-line bg-brand-bg-soft/80 px-1 text-brand-ink-2`}
      />
    )
  }

  if (SIGNING_KINDS.has(field.kind)) {
    const isInitials = field.kind === 'initials'
    const signed = adopted && typedName.trim()
    return (
      <button
        type="button"
        aria-label={label}
        aria-pressed={Boolean(adopted)}
        onClick={() => onAdopt(field)}
        style={{ ...style, fontSize: clamp(rect.height * 0.6, 12, 30) }}
        className={`${base} flex items-center justify-start overflow-hidden rounded border-2 border-dashed px-2 text-left transition-colors ${signed ? 'border-brand-green bg-brand-green/5' : 'border-brand-accent bg-brand-accent/10 hover:bg-brand-accent/20'}`}
      >
        {signed
          ? <span className="font-serif italic leading-none whitespace-nowrap" style={SIGNATURE_FONT}>{isInitials ? initialsFor(typedName) : typedName.trim()}</span>
          : <span className="text-[11px] font-semibold uppercase tracking-wide text-brand-accent">{isInitials ? 'Click to initial' : 'Click to sign'}</span>}
      </button>
    )
  }

  if (field.kind === 'date') {
    return (
      <input
        type="text"
        readOnly
        tabIndex={-1}
        aria-label={label}
        value={todayLabel()}
        style={style}
        className={`${base} rounded border border-brand-line bg-brand-bg-soft/80 px-1 text-brand-ink-2`}
      />
    )
  }

  if (field.kind === 'checkbox') {
    return (
      <input
        type="checkbox"
        aria-label={label}
        checked={value === 'true'}
        onChange={(event) => onChange(field, event.target.checked ? 'true' : 'false')}
        style={{ ...style, fontSize: undefined }}
        className={`${base} m-0 cursor-pointer rounded border border-brand-accent/60 bg-brand-accent/10 accent-brand-ink`}
      />
    )
  }

  if (field.kind === 'choice' || field.kind === 'radio') {
    return (
      <select
        aria-label={label}
        required={field.required}
        value={value ?? ''}
        onChange={(event) => onChange(field, event.target.value)}
        style={style}
        className={`${base} rounded border border-brand-accent/60 bg-brand-accent/10 px-1 focus:outline-none focus:ring-2 focus:ring-brand-accent/40`}
      >
        <option value="">Choose…</option>
        {optionsFor(field).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    )
  }

  const inputClass = `${base} rounded border border-brand-accent/60 bg-brand-accent/10 px-1 focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-accent/40`
  if (field.multiline) {
    return (
      <textarea
        aria-label={label}
        required={field.required}
        value={value ?? ''}
        onChange={(event) => onChange(field, event.target.value)}
        style={style}
        className={`${inputClass} resize-none leading-tight`}
      />
    )
  }
  return (
    <input
      type="text"
      aria-label={label}
      required={field.required}
      value={value ?? ''}
      onChange={(event) => onChange(field, event.target.value)}
      style={style}
      className={inputClass}
    />
  )
}

// One rendered page with its overlays. The viewport pdf.js reports after the
// draw is what turns PDF points into canvas pixels, and it is per page.
function SigningPage({ document, page, zoom, fields, interactive, onError, values, adopted, typedName, onChange, onAdopt }) {
  const [viewport, setViewport] = useState(null)
  const onViewport = useCallback((value) => setViewport(value), [])
  const rotated = Math.abs((page.rotation || 0) % 180) === 90
  const pageWidth = rotated ? page.height : page.width
  const pageHeight = rotated ? page.width : page.height
  return (
    <div
      className="relative mx-auto mb-4 bg-white shadow-sm last:mb-0"
      style={{ width: pageWidth * zoom, height: pageHeight * zoom }}
      data-testid={`signing-page-${page.page}`}
    >
      <PdfPageCanvas document={document} pageNumber={page.page} zoom={zoom} onViewport={onViewport} onError={onError} />
      {interactive && viewport && fields.map((field) => (
        <FieldOverlay
          key={field.field_id}
          field={field}
          rect={overlayToCanvasRect(field, page, viewport, zoom)}
          value={values[field.field_id]}
          adopted={Boolean(adopted[field.field_id])}
          typedName={typedName}
          onChange={onChange}
          onAdopt={onAdopt}
        />
      ))}
    </div>
  )
}

function StatusNote({ icon: Icon, tone, children }) {
  return (
    <p role="status" className={`flex items-start gap-2 text-sm ${tone === 'green' ? 'text-brand-green' : 'text-brand-ink'}`}>
      <Icon size={18} className={`mt-0.5 shrink-0 ${tone === 'green' ? 'text-brand-green' : 'text-brand-amber'}`} />
      <span>{children}</span>
    </p>
  )
}

function PaperPath({ request, busy, onUpload, prominent }) {
  const body = (
    <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-6">
      <a
        href={downloadClientPortalDocumentUrl(request.document_id)}
        className="inline-flex items-center gap-1.5 text-sm text-brand-accent hover:underline"
      >
        <Download size={16} /> Download document
      </a>
      <label className="flex flex-col gap-1 text-sm text-brand-ink sm:flex-row sm:items-center sm:gap-2">
        <span className="inline-flex items-center gap-1.5"><Upload size={16} className="text-brand-ink-2" /> Upload the signed copy (PDF)</span>
        <input
          type="file"
          accept="application/pdf"
          disabled={Boolean(busy)}
          onChange={(event) => { onUpload(event.target.files?.[0]); event.target.value = '' }}
          className="text-sm"
        />
      </label>
    </div>
  )
  if (prominent) {
    return (
      <div className="rounded-xl border border-brand-amber/40 bg-brand-amber/5 p-4">
        <p className="text-sm font-semibold text-brand-ink">Download, complete and sign, then upload the signed copy.</p>
        <p className="mt-1 text-xs text-brand-ink-2">Your legal team reviews the uploaded copy before it counts as signed.</p>
        {body}
      </div>
    )
  }
  return (
    <details className="rounded-xl border border-brand-line p-4">
      <summary className="cursor-pointer text-sm text-brand-ink-2">Prefer paper? Download, complete and sign, then upload the signed copy.</summary>
      {body}
    </details>
  )
}

/**
 * The client's in-document signing form: every page of the PDF drawn in a
 * continuous scroll, the request's fields laid over their rects as live inputs,
 * and the typed legal name adopted as the signature at each signature field.
 * `onChanged(result)` fires after a successful sign or upload so the host can
 * reload the request list.
 */
export default function ClientSignatureDocument({ request, onChanged, onSessionError }) {
  const [source, setSource] = useState(null)
  const [sourceError, setSourceError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [manifest, setManifest] = useState(null)
  const [manifestFailed, setManifestFailed] = useState(false)
  const [values, setValues] = useState({})
  const [adopted, setAdopted] = useState({})
  const [typedName, setTypedName] = useState('')
  const [consent, setConsent] = useState(false)
  const [hint, setHint] = useState('')
  const [error, setError] = useState('')
  const [renderError, setRenderError] = useState(false)
  const [busy, setBusy] = useState('')
  const [outcome, setOutcome] = useState(null)
  const [zoomMode, setZoomMode] = useState('fit')
  const [container, setContainer] = useState(null)
  const [width, setWidth] = useState(600)
  const nameRef = useRef(null)

  useEffect(() => {
    let active = true
    setSource(null); setSourceError(false); setRenderError(false)
    api.get(`/portal/client/documents/${request.document_id}/download`, { responseType: 'blob' })
      .then(({ data }) => { if (active) setSource(data) })
      .catch(() => { if (active) setSourceError(true) })
    return () => { active = false }
  }, [request.document_id, attempt])

  useEffect(() => {
    let active = true
    setManifest(null); setManifestFailed(false)
    getClientPortalSignatureFields(request.id)
      .then((data) => {
        if (!active) return
        setManifest(data)
        setValues(initialValues(Array.isArray(data?.fields) ? data.fields : []))
        setAdopted({})
      })
      .catch((e) => {
        if (!active) return
        if (!onSessionError?.(e)) setManifestFailed(true)
      })
    return () => { active = false }
  }, [request.id, attempt, onSessionError])

  const { document, pages, error: pdfError } = useTemplatePdfDocument(source)

  useEffect(() => {
    if (!container || typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(160, entry.contentRect.width - 32)))
    observer.observe(container)
    return () => observer.disconnect()
  }, [container])

  const onRenderError = useCallback(() => setRenderError(true), [])

  const fields = useMemo(() => (Array.isArray(manifest?.fields) ? manifest.fields : []), [manifest])
  const fieldsByPage = useMemo(() => {
    const grouped = {}
    for (const field of fields) {
      const page = Number(field.page) || 1
      if (!grouped[page]) grouped[page] = []
      grouped[page].push(field)
    }
    return grouped
  }, [fields])
  const mine = useMemo(() => fields.filter((field) => field.mine !== false), [fields])
  const requiredFields = useMemo(
    () => mine.filter((field) => SIGNING_KINDS.has(field.kind) || (FILLABLE_KINDS.has(field.kind) && field.required)),
    [mine],
  )

  const manifestLoading = !manifest && !manifestFailed
  // Without a manifest, a parseable PDF, or a drawable document there is nothing
  // to overlay — but the client can still download, sign on paper and upload, or
  // adopt a typed signature without filling the form.
  const fallbackMode = manifestFailed || manifest?.fill_supported === false || sourceError || Boolean(pdfError)
  const fillMode = Boolean(manifest) && !fallbackMode

  const isComplete = (field) => {
    if (SIGNING_KINDS.has(field.kind)) return Boolean(adopted[field.field_id])
    // A required checkbox on a form is an acknowledgment the client must tick.
    if (field.kind === 'checkbox') return values[field.field_id] === 'true'
    return Boolean(String(values[field.field_id] ?? '').trim())
  }
  const completedCount = requiredFields.filter(isComplete).length
  const fieldsReady = !fillMode || completedCount === requiredFields.length
  const canSign = fieldsReady && !manifestLoading && Boolean(typedName.trim()) && consent && !busy

  const widestPage = pages.reduce((widest, page) => {
    const rotated = Math.abs((page.rotation || 0) % 180) === 90
    return Math.max(widest, rotated ? page.height : page.width)
  }, 0) || 612
  const zoom = zoomMode === 'fit' ? Math.min(1.5, width / widestPage) : Number(zoomMode)

  const changeValue = (field, value) => setValues((previous) => ({ ...previous, [field.field_id]: value }))
  const adopt = (field) => {
    if (!typedName.trim()) {
      setHint('Type your full legal name in the bar below first, then click the signature field to place it.')
      nameRef.current?.focus()
      return
    }
    setHint('')
    setAdopted((previous) => ({ ...previous, [field.field_id]: true }))
  }

  const sign = async () => {
    setError(''); setHint(''); setBusy('signing')
    try {
      const field_values = {}
      if (fillMode) {
        for (const field of mine) {
          if (!FILLABLE_KINDS.has(field.kind)) continue
          field_values[field.field_id] = values[field.field_id] ?? (field.kind === 'checkbox' ? 'false' : '')
        }
      }
      const result = await signClientPortalSignature(request.id, {
        typed_signature: typedName.trim(),
        consent_to_electronic_signature: true,
        consent_text_version: CONSENT_TEXT_VERSION,
        field_values,
      })
      setOutcome({ kind: 'signed', result })
      await onChanged?.(result)
    } catch (e) {
      if (!onSessionError?.(e)) setError(errorMessage(e, 'Your signature could not be recorded. Please try again.'))
    } finally {
      setBusy('')
    }
  }

  const upload = async (file) => {
    if (!file) return
    setError(''); setBusy('uploading')
    try {
      const result = await uploadClientPortalSignedCopy(request.id, file)
      setOutcome({ kind: 'uploaded', result })
      await onChanged?.(result)
    } catch (e) {
      if (!onSessionError?.(e)) setError(errorMessage(e, 'The signed copy could not be uploaded. Please check it is a PDF and try again.'))
    } finally {
      setBusy('')
    }
  }

  if (request.submitted_document_id || outcome?.kind === 'uploaded') {
    return <StatusNote icon={Clock}>{SIGNED_COPY_RECEIVED_MESSAGE}</StatusNote>
  }
  if (request.completion_pending || outcome?.kind === 'signed') {
    return <StatusNote icon={CheckCircle2} tone="green">{signedOutcomeMessage(outcome?.result || request)}</StatusNote>
  }
  const me = manifest?.signer_id ? request.signers?.find((signer) => signer.id === manifest.signer_id) : null
  if (me?.status === 'signed') {
    return <StatusNote icon={CheckCircle2} tone="green">You have signed this document. It completes once every signer has signed.</StatusNote>
  }

  const nameId = `legal-name-${request.id}`
  return (
    <section aria-label={`Sign ${request.document_name || 'document'}`} className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-brand-ink-2">
        <span className="inline-flex items-center gap-1.5"><FileText size={14} /> {pages.length ? `${pages.length} page${pages.length === 1 ? '' : 's'}` : 'Document'}{fillMode ? ` · ${mine.length} field${mine.length === 1 ? '' : 's'} for you` : ''}</span>
        <label className="flex items-center gap-2">Zoom
          <select aria-label="Document zoom" value={zoomMode} onChange={(event) => setZoomMode(event.target.value)} className="rounded border border-brand-line bg-brand-surface p-1.5">
            <option value="fit">Fit width</option>
            <option value="1">100%</option>
            <option value="1.25">125%</option>
          </select>
        </label>
      </div>

      {fallbackMode && (
        <p className="text-sm text-brand-ink-2">
          {manifest?.fill_supported === false || manifestFailed
            ? 'This form cannot be filled in the browser.'
            : 'The document could not be displayed in the browser.'}{' '}
          Download it, complete and sign it, then upload the signed copy — or type your legal name below to sign electronically.
        </p>
      )}

      <div
        ref={setContainer}
        className="max-h-[70vh] min-h-64 overflow-auto rounded-xl border border-brand-line bg-brand-bg-soft p-4"
        aria-busy={!sourceError && !pdfError && pages.length === 0}
      >
        {sourceError ? (
          <p role="alert" className="text-sm text-brand-rose">
            The document could not be loaded.{' '}
            <button type="button" className="underline" onClick={() => setAttempt((value) => value + 1)}>Retry document</button>
          </p>
        ) : pdfError ? (
          <p role="alert" className="text-sm text-brand-rose">The document could not be displayed. Download it below to review every page.</p>
        ) : pages.length === 0 ? (
          <p role="status" className="text-sm text-brand-ink-2">Loading document to review…</p>
        ) : (
          <>
            {renderError && <p role="alert" className="mb-3 text-sm text-brand-rose">A page could not be displayed. Download the document to review it in full.</p>}
            {fillMode && manifestLoading && <p role="status" className="mb-3 text-sm text-brand-ink-2">Preparing your form…</p>}
            {pages.map((page) => (
              <SigningPage
                key={page.page}
                document={document}
                page={page}
                zoom={zoom}
                fields={fieldsByPage[page.page] || []}
                interactive={fillMode}
                onError={onRenderError}
                values={values}
                adopted={adopted}
                typedName={typedName}
                onChange={changeValue}
                onAdopt={adopt}
              />
            ))}
          </>
        )}
      </div>

      {fallbackMode && <PaperPath request={request} busy={busy} onUpload={upload} prominent />}

      <div className="sticky bottom-0 z-10 rounded-xl border border-brand-line bg-brand-surface p-4 shadow-lg">
        <label htmlFor={nameId} className="mb-1 block text-xs font-semibold uppercase tracking-wide text-brand-ink-2">
          Type your full legal name — this becomes your signature
        </label>
        <input
          id={nameId}
          ref={nameRef}
          value={typedName}
          autoComplete="name"
          onChange={(event) => { setTypedName(event.target.value); if (event.target.value.trim()) setHint('') }}
          placeholder="e.g. Jane A. Smith"
          className={INPUT_CLASS}
        />
        {typedName.trim() && (
          <p className="mt-2 text-xs text-brand-ink-2">
            Your signature: <span className="font-serif italic text-xl text-brand-ink" style={SIGNATURE_FONT}>{typedName.trim()}</span>
            {fillMode && requiredFields.some((field) => SIGNING_KINDS.has(field.kind) && !adopted[field.field_id]) && ' — click each signature field on the document to place it.'}
          </p>
        )}
        <label className="mt-3 flex items-start gap-2 text-xs text-brand-ink-2">
          <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} className="mt-0.5" required />
          <span>{CONSENT_TEXT}</span>
        </label>
        <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-brand-ink-2" aria-live="polite">
            {fillMode
              ? `${completedCount} of ${requiredFields.length} required fields complete`
              : manifestLoading ? 'Preparing your form…' : 'Your typed name is adopted as your signature.'}
          </p>
          <button type="button" onClick={sign} disabled={!canSign} className={`w-full sm:w-auto ${PRIMARY_BUTTON}`}>
            {busy === 'signing' ? 'Recording your signature…' : 'Sign document'}
          </button>
        </div>
        {hint && <p role="status" className="mt-2 text-xs text-brand-amber">{hint}</p>}
        {error && <p role="alert" className="mt-2 text-sm text-brand-rose">{error}</p>}
      </div>

      {!fallbackMode && <PaperPath request={request} busy={busy} onUpload={upload} />}
    </section>
  )
}
