import { useEffect, useMemo, useRef, useState } from 'react'
import { FileText, LibraryBig, X } from 'lucide-react'
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
const FALLBACK_UPLOAD_HINT = 'The client intake form\nRecent statements'

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

export default function PaperworkDrawer({
  matterId, documents = [], users = [], clientEmail = '',
  timeZone = 'America/Chicago', matterType = '', practiceArea = '',
  onClose, onSent,
}) {
  const [step, setStep] = useState(0)
  const [draft, setDraft] = useState({ ...emptyDraft, email: clientEmail })
  const [agreementFile, setAgreementFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pack, setPack] = useState(null)
  const [packNote, setPackNote] = useState('')
  const [attachedDocuments, setAttachedDocuments] = useState([])
  // The exact message the client would receive for the current draft. Rendered
  // server-side so it matches delivery byte for byte, with a sample token.
  const [preview, setPreview] = useState(null)
  const [previewChannel, setPreviewChannel] = useState('email')
  const [previewError, setPreviewError] = useState('')
  const [previewLoading, setPreviewLoading] = useState(false)
  // Which card opened the firm-template picker: the document it saves becomes
  // the fee agreement, the intake form, or an additional signing form.
  const [pickerTarget, setPickerTarget] = useState(null)
  const [uploadingForm, setUploadingForm] = useState(false)
  const [uploadingIntake, setUploadingIntake] = useState(false)
  // The standard pack is seeded once, and only while the firm has not touched
  // the questions or uploads: an in-flight load must never overwrite a reply.
  const touched = useRef(false)

  const set = (key, value) => {
    touched.current = true
    setDraft(previous => ({ ...previous, [key]: value }))
  }

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

  // The three common pieces are the fee agreement, the questionnaire, and the
  // intake form. The questionnaire starts on the matter type's own questions
  // rather than the generic default, so "Start this case" is not generic.
  useEffect(() => {
    let active = true
    async function loadPack() {
      try {
        const value = await getIntakeStarterPack(
          matterId ? { matter_id: matterId } : { matter_type: matterType, practice_area: practiceArea },
        )
        if (!active || !value) return
        setPack(value)
        if (touched.current) return
        setDraft(previous => ({
          ...previous,
          questions: value.questions.map(question => question.label).join('\n') || previous.questions,
          uploads: value.upload_requirements.map(upload => upload.label).join('\n'),
        }))
        setPackNote(`Loaded the ${value.practice_label} questions and requested uploads. Review them before sending.`)
      } catch {
        // Keep the generic defaults; the firm can still type its own.
      }
    }
    loadPack()
    return () => { active = false }
  }, [matterId, matterType, practiceArea])

  // The agreement select lists PDFs, plus whatever is currently chosen so a
  // template that rendered as Word is still visible as the fee agreement.
  const agreementChoices = useMemo(
    () => allDocuments.filter(document => isPdf(document) || document.id === draft.agreementDocumentId),
    [allDocuments, draft.agreementDocumentId],
  )
  const selectable = useMemo(
    () => allDocuments.filter(document => document.id !== draft.agreementDocumentId && document.id !== draft.intakeFormDocumentId),
    [allDocuments, draft.agreementDocumentId, draft.intakeFormDocumentId],
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
        return { ...previous, intakeFormDocumentId: document.id, intakeFormLabel: document.filename }
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

  const hasAgreement = Boolean(draft.agreementDocumentId || agreementFile)
  const hasIntakeForm = Boolean(draft.intakeFormDocumentId)
  const hasUploads = Boolean(draft.uploads.trim())
  // Any subset is allowed; a packet just has to carry at least one item.
  const hasItem = hasAgreement || draft.includeQuestionnaire || draft.forms.length > 0 || hasIntakeForm || hasUploads
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

  function setIntakeForm(documentId) {
    const document = allDocuments.find(item => item.id === documentId)
    set('intakeFormDocumentId', documentId)
    set('intakeFormLabel', document?.filename || '')
  }

  async function loadStarterPack() {
    setPackNote('Loading the standard questions…')
    try {
      const value = pack || await getIntakeStarterPack(
        matterId ? { matter_id: matterId } : { matter_type: matterType, practice_area: practiceArea },
      )
      setPack(value)
      set('questions', value.questions.map(question => question.label).join('\n'))
      set('uploads', value.upload_requirements.map(upload => upload.label).join('\n'))
      setPackNote(`Loaded the ${value.practice_label} questions and requested uploads. Review them before sending.`)
    } catch {
      setPackNote('Could not load the standard questions. Enter them below.')
    }
  }

  async function send() {
    setBusy(true)
    setError('')
    try {
      const packet = await startMatterIntake(matterId, paperworkOptions(draft, timeZone), agreementFile)
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
            <p className="mt-0.5 text-[13px] text-brand-muted">Choose what the client should sign or complete. Any of the three standard pieces can go on its own.</p>
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
              <div className={card}>
                <h3 className="mb-1 font-semibold text-brand-ink">Fee agreement <span className="font-normal text-brand-muted">(optional)</span></h3>
                <p className="mb-3 text-[12px] text-brand-muted">When included, signing it opens the portal and starts the follow-up clock. Skip it to send only the questionnaire, intake form, or requested uploads.</p>
                {agreementChoices.length > 0 && (
                  <label className="mb-3 block">
                    <span className={label}>From matter documents</span>
                    <select
                      className={field}
                      value={draft.agreementDocumentId}
                      onChange={event => {
                        set('agreementDocumentId', event.target.value)
                        set('forms', draft.forms.filter(form => form.documentId !== event.target.value))
                      }}
                    >
                      <option value="">Do not include a fee agreement</option>
                      {agreementChoices.map(document => <option key={document.id} value={document.id}>{document.filename}</option>)}
                    </select>
                  </label>
                )}
                {!draft.agreementDocumentId && (
                  <label className="block">
                    <span className={label}>Attorney-reviewed PDF</span>
                    <input type="file" accept=".pdf,application/pdf" onChange={event => setAgreementFile(event.target.files?.[0] || null)} className="w-full text-[13px] text-brand-ink file:mr-3 file:rounded-lg file:border file:border-brand-line file:bg-brand-surface file:px-3 file:py-1.5 file:text-[13px]" />
                  </label>
                )}
                <button
                  type="button"
                  onClick={() => setPickerTarget('agreement')}
                  className={`mt-3 ${linkButton}`}
                >
                  <LibraryBig size={14} aria-hidden="true" /> Prepare the fee agreement from a firm template
                </button>
              </div>

              <div className={card}>
                <label className="flex items-center gap-2 font-semibold text-brand-ink">
                  <input type="checkbox" checked={draft.includeQuestionnaire} onChange={event => set('includeQuestionnaire', event.target.checked)} />
                  Client questionnaire
                </label>
                <button type="button" onClick={loadStarterPack} className={`mt-2 ${linkButton}`}>
                  Reset to standard questions for this matter type
                </button>
                {packNote && <p role="status" className="mt-1 text-[12px] text-brand-muted">{packNote}</p>}
                {draft.includeQuestionnaire && (
                  <label className="mt-3 block">
                    <span className={label}>One question per line</span>
                    <textarea rows={4} className={field} value={draft.questions} onChange={event => set('questions', event.target.value)} />
                  </label>
                )}
                <label className="mt-3 block">
                  <span className={label}>Requested client uploads — one per line</span>
                  <textarea rows={3} className={field} value={draft.uploads} onChange={event => set('uploads', event.target.value)} placeholder={uploadHint} />
                </label>
              </div>

              <div className={card}>
                <h3 className="mb-1 font-semibold text-brand-ink">Client intake form</h3>
                <p className="mb-3 text-[12px] text-brand-muted">The client&apos;s own details, answered once and reused. Optional, and never a signature.</p>
                {allDocuments.length > 0 && (
                  <label className="mb-3 block">
                    <span className={label}>Choose the client intake form</span>
                    <select className={field} value={draft.intakeFormDocumentId} onChange={event => setIntakeForm(event.target.value)}>
                      <option value="">Do not include an intake form</option>
                      {allDocuments.map(document => <option key={document.id} value={document.id}>{document.filename}</option>)}
                    </select>
                  </label>
                )}
                <button
                  type="button"
                  onClick={() => setPickerTarget('intake')}
                  className={linkButton}
                >
                  <LibraryBig size={14} aria-hidden="true" /> Prepare the client intake form from a firm template
                </button>
                <label className="mt-3 block">
                  <span className={label}>Or upload a prepared form</span>
                  <input
                    type="file"
                    accept=".pdf,.docx,application/pdf"
                    onChange={event => uploadFile(event, 'intake', setUploadingIntake)}
                    disabled={uploadingIntake}
                    className="w-full text-[13px] text-brand-ink file:mr-3 file:rounded-lg file:border file:border-brand-line file:bg-brand-surface file:px-3 file:py-1.5 file:text-[13px] disabled:opacity-50"
                  />
                  {uploadingIntake && <span role="status" className="mt-1 block text-[12px] text-brand-muted">Uploading…</span>}
                </label>
              </div>

              <div className={card}>
                <h3 className="mb-1 font-semibold text-brand-ink">Additional forms</h3>
                <p className="mb-3 text-[12px] text-brand-muted">Each signing form gets its own signature request and status.</p>
                <label className="mb-3 block">
                  <span className={label}>Choose a file</span>
                  <input
                    type="file"
                    accept=".pdf,.docx,application/pdf"
                    onChange={event => uploadFile(event, 'form', setUploadingForm)}
                    disabled={uploadingForm}
                    className="w-full text-[13px] text-brand-ink file:mr-3 file:rounded-lg file:border file:border-brand-line file:bg-brand-surface file:px-3 file:py-1.5 file:text-[13px] disabled:opacity-50"
                  />
                  {uploadingForm && <span role="status" className="mt-1 block text-[12px] text-brand-muted">Uploading…</span>}
                </label>
                <button
                  type="button"
                  onClick={() => setPickerTarget('form')}
                  className={`mb-3 ${linkButton}`}
                >
                  <LibraryBig size={14} aria-hidden="true" /> Prepare an additional form from a firm template
                </button>
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
                              Track signature (PDF)
                            </label>
                          )}
                        </li>
                      )
                    })}
                  </ul>
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
                {draft.forms.map(form => (
                  <li key={form.documentId} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-brand-line bg-brand-surface px-3 py-2.5">
                    <span className="min-w-0 truncate text-[13px] text-brand-ink">{form.label}</span>
                    <DueDate id={`due-${form.documentId}`} value={form.due} onChange={value => updateForm(form.documentId, { due: value })} />
                  </li>
                ))}
                {draft.includeQuestionnaire && (
                  <li className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-brand-line bg-brand-surface px-3 py-2.5">
                    <span className="text-[13px] text-brand-ink">Client questionnaire</span>
                    <DueDate id="due-questionnaire" value={draft.questionnaireDue} onChange={value => set('questionnaireDue', value)} />
                  </li>
                )}
                {draft.uploads.trim() && (
                  <li className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-brand-line bg-brand-surface px-3 py-2.5">
                    <span className="text-[13px] text-brand-ink">Requested uploads</span>
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
                  {hasAgreement && <li>Fee agreement{draft.agreementDue ? ` — due ${draft.agreementDue}` : ''}</li>}
                  {draft.forms.map(form => (
                    <li key={form.documentId}>
                      {form.label}{form.requiresSignature ? ' (signature)' : ''}{form.due ? ` — due ${form.due}` : ''}
                    </li>
                  ))}
                  {hasIntakeForm && <li>{draft.intakeFormLabel || 'Client intake form'}</li>}
                  {draft.includeQuestionnaire && <li>Client questionnaire{draft.questionnaireDue ? ` — due ${draft.questionnaireDue}` : ''}</li>}
                  {draft.uploads.trim() && <li>{draft.uploads.trim().split('\n').length} requested upload(s){draft.uploadsDue ? ` — due ${draft.uploadsDue}` : ''}</li>}
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

              {!hasItem && <p role="alert" className="text-[13px] text-brand-rose">Add at least one document, the questionnaire, or a requested upload before sending.</p>}
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
