import { useEffect, useMemo, useState } from 'react'
import { FileText, LibraryBig, Upload, X } from 'lucide-react'
import { getIntakeStarterPack, getMatterDocuments, previewMatterPaperwork, uploadMatterDocument } from '../../api'
import { startMatterIntake } from '../MatterIntakePanel'
import MatterTemplatePicker from '../templates/MatterTemplatePicker'
import { emptyDraft, paperworkOptions } from './paperwork'

const STEPS = ['Documents', 'Deadlines', 'Send']

const field = 'block w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2 text-sm text-brand-ink focus:border-brand-accent focus:outline-none focus:ring-1 focus:ring-brand-accent'
const label = 'block text-[12px] font-semibold uppercase tracking-wider text-brand-muted mb-1.5'
const card = 'rounded-xl border border-brand-line bg-brand-bg-soft/40 p-4'
const linkButton = 'inline-flex items-center gap-1.5 text-[13px] font-semibold text-brand-accent underline'

// A neutral fallback when the matter type is unknown; the real examples come
// from the matter's own starter pack so a criminal or bankruptcy matter never
// sees family-law records as the hint.
const FALLBACK_UPLOAD_HINT = 'Recent statements\nCopies of any existing court orders'

// A label wrapped around a visually hidden file input. The browser's own file
// control reports "No file chosen" even when the card already names a chosen
// document, so the two read as contradicting each other.
const uploadControl = `${linkButton} cursor-pointer rounded focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-brand-accent`

function isPdf(document) {
  return document.content_type === 'application/pdf' || document.filename?.toLowerCase().endsWith('.pdf')
}

const RENDER_CONTENT_TYPES = {
  pdf: 'application/pdf',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  markdown: 'text/markdown',
}

// The template render response names the saved matter document but not its
// content type. Prefer the matter's own record so the drawer treats the new
// file exactly like one that was already attached; fall back to the response.
async function resolveRenderedDocument(matterId, response) {
  const id = response?.matter_document_id
  if (!id) return null
  const fallback = {
    id,
    filename: response.output_filename || response.filename || 'Generated document',
    content_type: RENDER_CONTENT_TYPES[response.output_format || response.format] || null,
  }
  try {
    const listed = await getMatterDocuments(matterId, { limit: 200 })
    const documents = Array.isArray(listed) ? listed : listed?.items || []
    return documents.find(document => document.id === id) || fallback
  } catch {
    return fallback
  }
}

const SUPPLIED_KEYS = {
  agreement: ['agreementDocumentId', null],
  intake: ['intakeFormDocumentId', 'intakeFormLabel'],
  questionnaire: ['questionnaireDocumentId', 'questionnaireLabel'],
}

function DueDate({ id, value, onChange, hint }) {
  return (
    <label htmlFor={id} className="flex items-center gap-2 text-[12px] text-brand-muted">
      <span className="whitespace-nowrap">Due</span>
      <input
        id={id}
        type="date"
        value={value}
        onChange={event => onChange(event.target.value)}
        className="min-h-9 rounded-lg border border-brand-line bg-brand-surface px-2 py-1 text-[12px] text-brand-ink"
      />
      {hint && <span className="hidden sm:inline">{hint}</span>}
    </label>
  )
}

// The fee agreement, the client intake form and the client questionnaire are
// each supplied the same three ways: a document already on the matter, one
// prepared from a firm template, or an upload. Whichever way it arrives it
// becomes the one selected matter document, so a card states that single
// choice in one place rather than leaving a dropdown and a file input each
// looking like a separate answer to the same question.
function DocumentCard({
  title, description, chooseLabel, noneLabel, prepareLabel, uploadLabel,
  accept = '.pdf,.docx,application/pdf', documents, value, onChoose, onPrepare,
  onUpload, uploading, requiresSignature, onRequiresSignature,
}) {
  return (
    <div className={card}>
      <h3 className="mb-1 font-semibold text-brand-ink">{title} <span className="font-normal text-brand-muted">(optional)</span></h3>
      <p className="mb-3 text-[12px] text-brand-muted">{description}</p>
      {documents.length > 0 ? (
        <label className="block">
          <span className={label}>{chooseLabel}</span>
          <select className={field} value={value} onChange={event => onChoose(event.target.value)}>
            <option value="">{noneLabel}</option>
            {documents.map(document => <option key={document.id} value={document.id}>{document.filename}</option>)}
          </select>
        </label>
      ) : (
        // With nothing on the matter to choose from there is still one line
        // saying where the card stands, so it reads the same either way.
        <p className="text-[13px] text-brand-muted">Nothing chosen yet.</p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2">
        <button type="button" onClick={onPrepare} className={linkButton}>
          <LibraryBig size={14} aria-hidden="true" /> {prepareLabel}
        </button>
        <label className={`${uploadControl}${uploading ? ' opacity-50' : ''}`}>
          <Upload size={14} aria-hidden="true" /> {uploadLabel}
          <input type="file" accept={accept} onChange={onUpload} disabled={uploading} className="sr-only" />
        </label>
      </div>
      {uploading && <p role="status" className="mt-2 text-[12px] text-brand-muted">Uploading…</p>}
      {value && onRequiresSignature && (
        <label className="mt-3 flex items-center gap-2 text-[13px] text-brand-ink">
          <input type="checkbox" checked={requiresSignature} onChange={event => onRequiresSignature(event.target.checked)} />
          Client signs this form
        </label>
      )}
    </div>
  )
}

export default function PaperworkDrawer({
  matterId, documents = [], users = [], clientEmail = '',
  timeZone = 'America/Chicago', matterType = '', practiceArea = '',
  onClose, onSent,
}) {
  const [step, setStep] = useState(0)
  const [draft, setDraft] = useState({ ...emptyDraft, email: clientEmail })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pack, setPack] = useState(null)
  const [packError, setPackError] = useState('')
  const [attachedDocuments, setAttachedDocuments] = useState([])
  // The exact message the client would receive for the current draft. Rendered
  // server-side so it matches delivery byte for byte, with a sample token.
  const [preview, setPreview] = useState(null)
  const [previewChannel, setPreviewChannel] = useState('email')
  const [previewError, setPreviewError] = useState('')
  const [previewLoading, setPreviewLoading] = useState(false)
  // Which card opened the firm-template picker: the document it saves becomes
  // the fee agreement, the intake form, the questionnaire, or an additional
  // signing form.
  const [pickerTarget, setPickerTarget] = useState(null)
  const [uploadingForm, setUploadingForm] = useState(false)
  const [uploadingAgreement, setUploadingAgreement] = useState(false)
  const [uploadingIntake, setUploadingIntake] = useState(false)
  const [uploadingQuestionnaire, setUploadingQuestionnaire] = useState(false)

  const set = (key, value) => setDraft(previous => ({ ...previous, [key]: value }))

  // Documents prepared from a firm template or uploaded here are saved to the
  // matter and land in this list; merge them with the ones the matter already
  // had so either source can become the fee agreement or a signing form.
  const allDocuments = useMemo(() => {
    const seen = new Set()
    return [...attachedDocuments, ...documents].filter(document => {
      if (!document?.id || seen.has(document.id)) return false
      seen.add(document.id)
      return true
    })
  }, [attachedDocuments, documents])

  // The card opens this drawer immediately and resolves the client's address
  // afterwards, so seed the field when it arrives -- but never overwrite an
  // address the firm has already typed.
  useEffect(() => {
    if (!clientEmail) return
    setDraft(previous => (previous.email ? previous : { ...previous, email: clientEmail }))
  }, [clientEmail])

  // The starter pack names the practice area and suggests the records a
  // client of this matter type usually has to hand. It is only ever offered:
  // the records section starts empty and off so nothing is requested by
  // accident.
  useEffect(() => {
    let active = true
    async function loadPack() {
      try {
        const value = await getIntakeStarterPack(
          matterId ? { matter_id: matterId } : { matter_type: matterType, practice_area: practiceArea },
        )
        if (active && value) setPack(value)
      } catch {
        // The firm can still type its own list.
      }
    }
    loadPack()
    return () => { active = false }
  }, [matterId, matterType, practiceArea])

  // Whatever became the fee agreement, intake form, or questionnaire is not
  // offered again as another piece or as an additional form.
  const { agreementDocumentId, intakeFormDocumentId, questionnaireDocumentId } = draft
  // The agreement list is PDFs only, plus whatever is currently chosen so a
  // template that rendered as Word is still visible as the fee agreement.
  const agreementChoices = useMemo(
    () => allDocuments.filter(document => (isPdf(document) || document.id === agreementDocumentId)
      && ![intakeFormDocumentId, questionnaireDocumentId].includes(document.id)),
    [allDocuments, agreementDocumentId, intakeFormDocumentId, questionnaireDocumentId],
  )
  const selectable = useMemo(
    () => allDocuments.filter(document => ![agreementDocumentId, intakeFormDocumentId, questionnaireDocumentId].includes(document.id)),
    [allDocuments, agreementDocumentId, intakeFormDocumentId, questionnaireDocumentId],
  )
  const uploadHint = useMemo(() => {
    const labels = (pack?.upload_requirements || []).map(upload => upload.label).filter(Boolean)
    return labels.length ? labels.slice(0, 2).join('\n') : FALLBACK_UPLOAD_HINT
  }, [pack])

  // A document saved to the matter from this drawer joins the list and is
  // pre-selected in the card that opened the picker.
  function attachDocument(document, target) {
    if (!document?.id) return
    setAttachedDocuments(current => [document, ...current.filter(item => item.id !== document.id)])
    setDraft(previous => {
      if (target === 'agreement') {
        return {
          ...previous,
          agreementDocumentId: document.id,
          forms: previous.forms.filter(form => form.documentId !== document.id),
        }
      }
      if (target === 'intake') {
        return { ...previous, intakeFormDocumentId: document.id, intakeFormLabel: document.filename, forms: previous.forms.filter(form => form.documentId !== document.id) }
      }
      if (target === 'questionnaire') {
        return { ...previous, questionnaireDocumentId: document.id, questionnaireLabel: document.filename, forms: previous.forms.filter(form => form.documentId !== document.id) }
      }
      return {
        ...previous,
        forms: [
          ...previous.forms.filter(form => form.documentId !== document.id),
          { documentId: document.id, label: document.filename, requiresSignature: true, due: '' },
        ],
      }
    })
  }

  // The firm-template picker renders and saves to this matter itself; the
  // drawer only needs the resulting matter document.
  async function attachRendered(response) {
    const target = pickerTarget
    const document = await resolveRenderedDocument(matterId, response)
    if (document) attachDocument(document, target)
  }

  async function uploadFile(event, target, setUploading) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setUploading(true)
    setError('')
    try {
      const form = new FormData()
      form.append('file', file)
      attachDocument(await uploadMatterDocument(matterId, form), target)
    } catch (caught) {
      const detail = caught?.response?.data?.detail
      setError(typeof detail === 'string' ? detail : 'The document could not be uploaded. Please try again.')
    } finally {
      setUploading(false)
    }
  }

  const hasAgreement = Boolean(draft.agreementDocumentId)
  const hasIntakeForm = Boolean(draft.intakeFormDocumentId)
  const hasQuestionnaire = Boolean(draft.questionnaireDocumentId)
  const hasUploads = draft.requestUploads && Boolean(draft.uploads.trim())
  const uploadCount = hasUploads ? draft.uploads.trim().split('\n').filter(line => line.trim()).length : 0
  // Any subset is allowed; a packet just has to carry at least one item.
  const hasItem = hasAgreement || hasIntakeForm || hasQuestionnaire || draft.forms.length > 0 || hasUploads
  const canSend = Boolean(draft.email) && draft.channels.length > 0
    && (!draft.channels.includes('sms') || draft.smsPermissionVerified) && hasItem

  function toggleForm(document, checked) {
    set('forms', checked
      ? [...draft.forms, { documentId: document.id, label: document.filename, requiresSignature: isPdf(document), due: '' }]
      : draft.forms.filter(form => form.documentId !== document.id))
  }

  function updateForm(documentId, patch) {
    set('forms', draft.forms.map(form => (form.documentId === documentId ? { ...form, ...patch } : form)))
  }

  // Choosing an existing matter document as one of the three standard pieces
  // also takes it out of the additional forms so it is never sent twice. The
  // fee agreement carries its own label server-side and so has none to set.
  function chooseSupplied(target, documentId) {
    const document = allDocuments.find(item => item.id === documentId)
    const [idKey, labelKey] = SUPPLIED_KEYS[target]
    setDraft(previous => ({
      ...previous,
      [idKey]: documentId,
      ...(labelKey ? { [labelKey]: document?.filename || '' } : {}),
      forms: documentId ? previous.forms.filter(form => form.documentId !== documentId) : previous.forms,
    }))
  }

  const practiceLabel = pack?.practice_label || 'this matter type'

  async function useSuggestedRecords() {
    setPackError('')
    try {
      const value = pack || await getIntakeStarterPack(
        matterId ? { matter_id: matterId } : { matter_type: matterType, practice_area: practiceArea },
      )
      setPack(value)
      const labels = (value?.upload_requirements || []).map(upload => upload.label).filter(Boolean)
      if (!labels.length) { setPackError('There is no suggested list for this matter type yet. Type the records to request.'); return }
      set('uploads', labels.join('\n'))
    } catch {
      setPackError('Could not load the suggested list. Type the records to request.')
    }
  }

  async function send() {
    setBusy(true)
    setError('')
    try {
      const packet = await startMatterIntake(matterId, paperworkOptions(draft, timeZone))
      onSent?.(packet)
      onClose?.()
    } catch (caught) {
      const detail = caught?.response?.data?.detail
      setError(typeof detail === 'string' ? detail : 'Could not send the paperwork. Check the details and try again.')
    } finally {
      setBusy(false)
    }
  }

  // The preview is a server round-trip so it cannot drift from the sent copy.
  // It waits for a sendable draft and debounces while the firm is still typing.
  useEffect(() => {
    if (step !== 2 || !draft.email || !hasItem || draft.channels.length === 0) {
      setPreview(null)
      setPreviewLoading(false)
      setPreviewError('')
      return undefined
    }
    let active = true
    const timer = setTimeout(async () => {
      setPreviewLoading(true)
      setPreviewError('')
      try {
        const value = await previewMatterPaperwork(matterId, paperworkOptions(draft, timeZone))
        if (active) setPreview(value)
      } catch (caught) {
        if (active) {
          const detail = caught?.response?.data?.detail
          setPreview(null)
          setPreviewError(typeof detail === 'string' ? detail : 'Could not render the preview yet.')
        }
      } finally {
        if (active) setPreviewLoading(false)
      }
    }, 350)
    return () => { active = false; clearTimeout(timer) }
  }, [step, matterId, timeZone, draft, hasItem])

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-brand-ink/30" role="dialog" aria-modal="true" aria-label="Send client paperwork">
      <div className="flex h-full w-full max-w-xl flex-col bg-brand-surface shadow-2xl">
        <header className="flex items-start justify-between border-b border-brand-line px-6 py-5">
          <div>
            <h2 className="font-serif text-xl font-bold text-brand-ink">Send client paperwork</h2>
            <p className="mt-0.5 text-[13px] text-brand-muted">Choose the forms the client signs in their portal and any records they should send. Any piece can go on its own.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="min-h-11 min-w-11 text-brand-muted hover:text-brand-ink">
            <X size={18} />
          </button>
        </header>

        <nav aria-label="Paperwork steps" className="flex gap-1 border-b border-brand-line px-6">
          {STEPS.map((name, index) => (
            <button
              key={name}
              type="button"
              onClick={() => setStep(index)}
              aria-current={step === index ? 'step' : undefined}
              className={`-mb-px border-b-2 px-4 py-3 text-[13px] font-semibold ${step === index ? 'border-brand-ink text-brand-ink' : 'border-transparent text-brand-muted hover:text-brand-ink-2'}`}
            >
              {index + 1}. {name}
            </button>
          ))}
        </nav>

        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {step === 0 && (
            <>
              <DocumentCard
                title="Fee agreement"
                description="When included, signing it opens the portal and starts the follow-up clock. The client always signs it. Skip it to send only the other forms or requested records."
                chooseLabel="Choose the fee agreement"
                noneLabel="Do not include a fee agreement"
                prepareLabel="Prepare the fee agreement from a firm template"
                uploadLabel="Upload a prepared fee agreement"
                accept=".pdf,application/pdf"
                documents={agreementChoices}
                value={draft.agreementDocumentId}
                onChoose={value => chooseSupplied('agreement', value)}
                onPrepare={() => setPickerTarget('agreement')}
                onUpload={event => uploadFile(event, 'agreement', setUploadingAgreement)}
                uploading={uploadingAgreement}
              />

              <DocumentCard
                title="Client intake form"
                description="The client's own details, filled in and signed inside the form in their portal."
                chooseLabel="Choose the client intake form"
                noneLabel="Do not include an intake form"
                prepareLabel="Prepare the client intake form from a firm template"
                uploadLabel="Upload a prepared intake form"
                documents={allDocuments.filter(document => document.id !== draft.agreementDocumentId && document.id !== draft.questionnaireDocumentId)}
                value={draft.intakeFormDocumentId}
                onChoose={value => chooseSupplied('intake', value)}
                onPrepare={() => setPickerTarget('intake')}
                onUpload={event => uploadFile(event, 'intake', setUploadingIntake)}
                uploading={uploadingIntake}
                requiresSignature={draft.intakeFormRequiresSignature}
                onRequiresSignature={value => set('intakeFormRequiresSignature', value)}
              />

              <DocumentCard
                title="Client questionnaire"
                description="Your matter-type questions as a PDF form. The client answers and signs it inside the document, exactly like the intake form."
                chooseLabel="Choose the client questionnaire"
                noneLabel="Do not include a questionnaire"
                prepareLabel="Prepare the client questionnaire from a firm template"
                uploadLabel="Upload a prepared questionnaire"
                documents={allDocuments.filter(document => document.id !== draft.agreementDocumentId && document.id !== draft.intakeFormDocumentId)}
                value={draft.questionnaireDocumentId}
                onChoose={value => chooseSupplied('questionnaire', value)}
                onPrepare={() => setPickerTarget('questionnaire')}
                onUpload={event => uploadFile(event, 'questionnaire', setUploadingQuestionnaire)}
                uploading={uploadingQuestionnaire}
                requiresSignature={draft.questionnaireRequiresSignature}
                onRequiresSignature={value => set('questionnaireRequiresSignature', value)}
              />

              <div className={card}>
                <h3 className="mb-1 font-semibold text-brand-ink">Additional forms</h3>
                <p className="mb-3 text-[12px] text-brand-muted">Each signed form gets its own signature request and status.</p>
                <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-2">
                  <button type="button" onClick={() => setPickerTarget('form')} className={linkButton}>
                    <LibraryBig size={14} aria-hidden="true" /> Prepare an additional form from a firm template
                  </button>
                  <label className={`${uploadControl}${uploadingForm ? ' opacity-50' : ''}`}>
                    <Upload size={14} aria-hidden="true" /> Upload an additional form
                    <input
                      type="file"
                      accept=".pdf,.docx,application/pdf"
                      onChange={event => uploadFile(event, 'form', setUploadingForm)}
                      disabled={uploadingForm}
                      className="sr-only"
                    />
                  </label>
                </div>
                {uploadingForm && <p role="status" className="mb-3 text-[12px] text-brand-muted">Uploading…</p>}
                {selectable.length === 0 ? (
                  <p className="text-[13px] text-brand-muted">Attach templates in Documents to include them here.</p>
                ) : (
                  <ul className="space-y-2">
                    {selectable.map(document => {
                      const chosen = draft.forms.find(form => form.documentId === document.id)
                      return (
                        <li key={document.id} className="rounded-lg border border-brand-line bg-brand-surface px-3 py-2">
                          <label className="flex items-center gap-2 text-[13px] text-brand-ink">
                            <input type="checkbox" checked={Boolean(chosen)} onChange={event => toggleForm(document, event.target.checked)} />
                            <FileText size={14} className="shrink-0 text-brand-muted" />
                            <span className="truncate">{document.filename}</span>
                          </label>
                          {chosen && (
                            <label className="mt-2 ml-6 flex items-center gap-2 text-[12px] text-brand-muted">
                              <input type="checkbox" checked={chosen.requiresSignature} onChange={event => updateForm(document.id, { requiresSignature: event.target.checked })} />
                              Client signs this form
                            </label>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>

              <div className={card}>
                <label className="flex items-center gap-2 font-semibold text-brand-ink">
                  <input type="checkbox" checked={draft.requestUploads} onChange={event => set('requestUploads', event.target.checked)} />
                  Records to request from the client <span className="font-normal text-brand-muted">(optional)</span>
                </label>
                <p className="mt-1 text-[12px] text-brand-muted">Documents the client already has, such as statements or existing orders. They appear in the client&apos;s portal as &ldquo;Records to send us&rdquo; and are uploaded, not signed.</p>
                {draft.requestUploads && (
                  <>
                    <label className="mt-3 block">
                      <span className={label}>One record per line</span>
                      <textarea rows={4} className={field} value={draft.uploads} onChange={event => set('uploads', event.target.value)} placeholder={uploadHint} />
                    </label>
                    <button type="button" onClick={useSuggestedRecords} className={`mt-2 ${linkButton}`}>
                      Use the suggested list for {practiceLabel}
                    </button>
                    {packError && <p role="status" className="mt-1 text-[12px] text-brand-muted">{packError}</p>}
                  </>
                )}
              </div>
            </>
          )}

          {step === 1 && (
            <>
              <p className="text-[13px] text-brand-muted">
                A due date creates an assigned follow-up task on this matter, due at 5pm in the client&apos;s timezone.
                Leave a date blank to rely on the standard reminders.
              </p>
              <ul className="space-y-2">
                {hasAgreement && (
                  <li className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-brand-line bg-brand-surface px-3 py-2.5">
                    <span className="text-[13px] font-semibold text-brand-ink">Fee agreement</span>
                    <DueDate id="due-fee-agreement" value={draft.agreementDue} onChange={value => set('agreementDue', value)} />
                  </li>
                )}
                {hasIntakeForm && (
                  <li className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-brand-line bg-brand-surface px-3 py-2.5">
                    <span className="min-w-0 truncate text-[13px] text-brand-ink">{draft.intakeFormLabel || 'Client intake form'}</span>
                    <DueDate id="due-intake-form" value={draft.intakeFormDue} onChange={value => set('intakeFormDue', value)} />
                  </li>
                )}
                {hasQuestionnaire && (
                  <li className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-brand-line bg-brand-surface px-3 py-2.5">
                    <span className="min-w-0 truncate text-[13px] text-brand-ink">{draft.questionnaireLabel || 'Client questionnaire'}</span>
                    <DueDate id="due-questionnaire" value={draft.questionnaireDue} onChange={value => set('questionnaireDue', value)} />
                  </li>
                )}
                {draft.forms.map(form => (
                  <li key={form.documentId} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-brand-line bg-brand-surface px-3 py-2.5">
                    <span className="min-w-0 truncate text-[13px] text-brand-ink">{form.label}</span>
                    <DueDate id={`due-${form.documentId}`} value={form.due} onChange={value => updateForm(form.documentId, { due: value })} />
                  </li>
                ))}
                {hasUploads && (
                  <li className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-brand-line bg-brand-surface px-3 py-2.5">
                    <span className="text-[13px] text-brand-ink">Requested records</span>
                    <DueDate id="due-uploads" value={draft.uploadsDue} onChange={value => set('uploadsDue', value)} />
                  </li>
                )}
              </ul>
            </>
          )}

          {step === 2 && (
            <>
              <div className={card}>
                <label className="block">
                  <span className={label}>Client email</span>
                  <input type="email" className={field} value={draft.email} onChange={event => set('email', event.target.value)} />
                </label>
                <div className="mt-3 flex flex-wrap gap-4">
                  {['email', 'sms'].map(channel => (
                    <label key={channel} className="flex items-center gap-2 text-[13px] text-brand-ink">
                      <input
                        type="checkbox"
                        checked={draft.channels.includes(channel)}
                        onChange={event => set('channels', event.target.checked
                          ? [...draft.channels, channel]
                          : draft.channels.filter(item => item !== channel))}
                      />
                      {channel === 'email' ? 'Email' : 'SMS'}
                    </label>
                  ))}
                </div>
                {draft.channels.includes('sms') && (
                  <>
                    <label className="mt-3 flex items-start gap-2 text-[12px] text-brand-muted">
                      <input type="checkbox" className="mt-0.5" checked={draft.smsPermissionVerified} onChange={event => set('smsPermissionVerified', event.target.checked)} />
                      I verified this client&apos;s mobile number and recorded permission for intake texts. Existing opt-outs remain in effect.
                    </label>
                    <label className="mt-2 flex items-start gap-2 text-[12px] text-brand-muted">
                      <input type="checkbox" className="mt-0.5" checked={draft.smsCaseUpdatesVerified} onChange={event => set('smsCaseUpdatesVerified', event.target.checked)} />
                      The client also agreed to texts about this case after onboarding — signature requests and deadline reminders. Without this, texting stops when intake does.
                    </label>
                  </>
                )}
                {users.length > 0 && (
                  <label className="mt-3 block">
                    <span className={label}>Responsible staff</span>
                    <select className={field} value={draft.ownerId} onChange={event => set('ownerId', event.target.value)}>
                      <option value="">Assign to me</option>
                      {users.map(person => <option key={person.id} value={person.id}>{person.full_name || person.email}</option>)}
                    </select>
                  </label>
                )}
              </div>

              <div className={card}>
                <h3 className="mb-2 font-semibold text-brand-ink">Going out now</h3>
                <ul className="space-y-1 text-[13px] text-brand-ink-2">
                  {hasAgreement && <li>Fee agreement (signature){draft.agreementDue ? ` — due ${draft.agreementDue}` : ''}</li>}
                  {hasIntakeForm && <li>{draft.intakeFormLabel || 'Client intake form'}{draft.intakeFormRequiresSignature ? ' (signature)' : ''}{draft.intakeFormDue ? ` — due ${draft.intakeFormDue}` : ''}</li>}
                  {hasQuestionnaire && <li>{draft.questionnaireLabel || 'Client questionnaire'}{draft.questionnaireRequiresSignature ? ' (signature)' : ''}{draft.questionnaireDue ? ` — due ${draft.questionnaireDue}` : ''}</li>}
                  {draft.forms.map(form => (
                    <li key={form.documentId}>
                      {form.label}{form.requiresSignature ? ' (signature)' : ''}{form.due ? ` — due ${form.due}` : ''}
                    </li>
                  ))}
                  {hasUploads && <li>{uploadCount} requested record{uploadCount === 1 ? '' : 's'}{draft.uploadsDue ? ` — due ${draft.uploadsDue}` : ''}</li>}
                </ul>
                <p className="mt-3 text-[12px] text-brand-muted">
                  Signing a fee agreement opens the client portal and creates a follow-up due within 24 hours.
                  Without one, the portal opens on the first message. SMS respects recorded permission and quiet hours.
                </p>
              </div>

              <div className={card}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="font-semibold text-brand-ink">Message the client will receive</h3>
                  <div role="tablist" aria-label="Preview channel" className="flex gap-1">
                    {[['email', 'Email'], ['sms', 'Text']].map(([channel, name]) => (
                      <button
                        key={channel}
                        type="button"
                        role="tab"
                        aria-selected={previewChannel === channel}
                        onClick={() => setPreviewChannel(channel)}
                        className={`rounded-lg px-3 py-1 text-[12px] font-semibold ${previewChannel === channel ? 'bg-brand-ink text-white' : 'border border-brand-line text-brand-muted hover:text-brand-ink'}`}
                      >
                        {name}
                      </button>
                    ))}
                  </div>
                </div>
                {previewLoading && <p role="status" className="mt-3 text-[12px] text-brand-muted">Rendering the message…</p>}
                {previewError && <p role="alert" className="mt-3 text-[12px] text-brand-rose">{previewError}</p>}
                {preview && previewChannel === 'email' && (
                  <div className="mt-3">
                    <p className="text-[12px] font-semibold uppercase tracking-wider text-brand-muted">Subject</p>
                    <p className="text-[13px] font-semibold text-brand-ink">{preview.subject}</p>
                    <p className="mt-2 text-[12px] text-brand-muted">The link below is a sample; the real one is created when you send.</p>
                    <iframe
                      title="Client email preview"
                      sandbox=""
                      srcDoc={preview.html_body}
                      className="mt-2 h-96 w-full rounded-lg border border-brand-line bg-white"
                    />
                  </div>
                )}
                {preview && previewChannel === 'sms' && (
                  <div className="mt-3">
                    <p className="text-[12px] font-semibold uppercase tracking-wider text-brand-muted">Text message</p>
                    <p className="mt-1 whitespace-pre-wrap rounded-lg border border-brand-line bg-brand-surface px-3 py-2 text-[13px] text-brand-ink">{preview.sms_body}</p>
                  </div>
                )}
                {!preview && !previewLoading && !previewError && (
                  <p className="mt-3 text-[12px] text-brand-muted">Add a client email and at least one item to preview the message.</p>
                )}
              </div>

              {!hasItem && <p role="alert" className="text-[13px] text-brand-rose">Add at least one form or a requested record before sending.</p>}
            </>
          )}

          {error && <p role="alert" className="rounded-lg border border-brand-rose/30 bg-brand-rose/10 px-3 py-2 text-[13px] text-brand-rose">{error}</p>}
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-brand-line px-6 py-4">
          <button type="button" onClick={onClose} className="min-h-11 px-3 text-[13px] font-semibold text-brand-muted hover:text-brand-ink">Cancel</button>
          <div className="flex items-center gap-2">
            {step > 0 && (
              <button type="button" onClick={() => setStep(step - 1)} className="min-h-11 rounded-lg border border-brand-line px-4 text-[13px] font-semibold text-brand-ink">Back</button>
            )}
            {step < STEPS.length - 1 ? (
              <button type="button" onClick={() => setStep(step + 1)} className="min-h-11 rounded-lg bg-brand-ink px-5 text-[13px] font-semibold text-white">Next</button>
            ) : (
              <button type="button" disabled={busy || !canSend} onClick={send} className="min-h-11 rounded-lg bg-brand-ink px-5 text-[13px] font-semibold text-white disabled:opacity-50">
                {busy ? 'Sending…' : 'Send paperwork'}
              </button>
            )}
          </div>
        </footer>
      </div>
      {pickerTarget && (
        <MatterTemplatePicker
          matterId={matterId}
          onSaved={attachRendered}
          onClose={() => setPickerTarget(null)}
        />
      )}
    </div>
  )
}
