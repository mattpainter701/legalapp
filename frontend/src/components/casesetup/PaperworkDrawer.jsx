import { useEffect, useMemo, useState } from 'react'
import { FileText, LibraryBig, X } from 'lucide-react'
import { getIntakeStarterPack } from '../../api'
import { startMatterIntake } from '../MatterIntakePanel'
import FormLibraryDialog from './FormLibraryDialog'
import { emptyDraft, paperworkOptions } from './paperwork'

const STEPS = ['Documents', 'Deadlines', 'Send']

const field = 'block w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2 text-sm text-brand-ink focus:border-brand-accent focus:outline-none focus:ring-1 focus:ring-brand-accent'
const label = 'block text-[12px] font-semibold uppercase tracking-wider text-brand-muted mb-1.5'
const card = 'rounded-xl border border-brand-line bg-brand-bg-soft/40 p-4'

function isPdf(document) {
  return document.content_type === 'application/pdf' || document.filename?.toLowerCase().endsWith('.pdf')
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
  const [packNote, setPackNote] = useState('')
  const [attachedDocuments, setAttachedDocuments] = useState([])
  const [libraryTarget, setLibraryTarget] = useState(null)

  const set = (key, value) => setDraft(previous => ({ ...previous, [key]: value }))

  // Forms filled from the library are uploaded as matter documents and land
  // here; merge them with the ones the matter already had so either source can
  // become the fee agreement or an additional signing form.
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
  const pdfs = useMemo(() => allDocuments.filter(isPdf), [allDocuments])
  const selectable = useMemo(
    () => allDocuments.filter(document => document.id !== draft.agreementDocumentId),
    [allDocuments, draft.agreementDocumentId],
  )

  function attachFilled(document) {
    if (!document?.id) return
    setAttachedDocuments(current => [document, ...current])
    setDraft(previous => {
      if (libraryTarget === 'agreement') {
        return {
          ...previous,
          agreementDocumentId: document.id,
          forms: previous.forms.filter(form => form.documentId !== document.id),
        }
      }
      if (libraryTarget === 'form') {
        return {
          ...previous,
          forms: [
            ...previous.forms.filter(form => form.documentId !== document.id),
            { documentId: document.id, label: document.filename, requiresSignature: true, due: '' },
          ],
        }
      }
      return previous
    })
  }
  const hasAgreement = Boolean(draft.agreementDocumentId || agreementFile)
  const canSend = hasAgreement && draft.email && draft.channels.length > 0
    && (!draft.channels.includes('sms') || draft.smsPermissionVerified)

  function toggleForm(document, checked) {
    set('forms', checked
      ? [...draft.forms, { documentId: document.id, label: document.filename, requiresSignature: isPdf(document), due: '' }]
      : draft.forms.filter(form => form.documentId !== document.id))
  }

  function updateForm(documentId, patch) {
    set('forms', draft.forms.map(form => (form.documentId === documentId ? { ...form, ...patch } : form)))
  }

  async function loadStarterPack() {
    setPackNote('Loading the standard questions…')
    try {
      const pack = await getIntakeStarterPack(matterId ? { matter_id: matterId } : { matter_type: matterType, practice_area: practiceArea })
      setDraft(previous => ({
        ...previous,
        questions: pack.questions.map(question => question.label).join('\n'),
        uploads: pack.upload_requirements.map(upload => upload.label).join('\n'),
      }))
      setPackNote(`Loaded the ${pack.practice_label} questions and requested uploads. Review them before sending.`)
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

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-brand-ink/30" role="dialog" aria-modal="true" aria-label="Send client paperwork">
      <div className="flex h-full w-full max-w-xl flex-col bg-brand-surface shadow-2xl">
        <header className="flex items-start justify-between border-b border-brand-line px-6 py-5">
          <div>
            <h2 className="font-serif text-xl font-bold text-brand-ink">Send client paperwork</h2>
            <p className="mt-0.5 text-[13px] text-brand-muted">The fee agreement opens the portal and starts the follow-up clock.</p>
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
                <h3 className="mb-3 font-semibold text-brand-ink">Fee agreement</h3>
                {pdfs.length > 0 && (
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
                      <option value="">Upload a reviewed PDF instead</option>
                      {pdfs.map(document => <option key={document.id} value={document.id}>{document.filename}</option>)}
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
                  onClick={() => setLibraryTarget('agreement')}
                  className="mt-3 inline-flex items-center gap-1.5 text-[13px] font-semibold text-brand-accent underline"
                >
                  <LibraryBig size={14} aria-hidden="true" /> Fill a firm template or sample
                </button>
              </div>

              <div className={card}>
                <h3 className="mb-1 font-semibold text-brand-ink">Additional forms</h3>
                <p className="mb-3 text-[12px] text-brand-muted">Each signing form gets its own signature request and status.</p>
                <button
                  type="button"
                  onClick={() => setLibraryTarget('form')}
                  className="mb-3 inline-flex items-center gap-1.5 text-[13px] font-semibold text-brand-accent underline"
                >
                  <LibraryBig size={14} aria-hidden="true" /> Fill a form from the library
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

              <div className={card}>
                <label className="flex items-center gap-2 font-semibold text-brand-ink">
                  <input type="checkbox" checked={draft.includeQuestionnaire} onChange={event => set('includeQuestionnaire', event.target.checked)} />
                  Include client questionnaire
                </label>
                <button type="button" onClick={loadStarterPack} className="mt-2 text-[13px] font-semibold text-brand-accent underline">
                  Use the standard questions for this matter type
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
                  <textarea rows={3} className={field} value={draft.uploads} onChange={event => set('uploads', event.target.value)} placeholder="Marriage certificate&#10;Recent bank statements" />
                </label>
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
                <li className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-brand-line bg-brand-surface px-3 py-2.5">
                  <span className="text-[13px] font-semibold text-brand-ink">Fee agreement</span>
                  <DueDate id="due-fee-agreement" value={draft.agreementDue} onChange={value => set('agreementDue', value)} />
                </li>
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
                  <li>Fee agreement{draft.agreementDue ? ` — due ${draft.agreementDue}` : ''}</li>
                  {draft.forms.map(form => (
                    <li key={form.documentId}>
                      {form.label}{form.requiresSignature ? ' (signature)' : ''}{form.due ? ` — due ${form.due}` : ''}
                    </li>
                  ))}
                  {draft.includeQuestionnaire && <li>Client questionnaire{draft.questionnaireDue ? ` — due ${draft.questionnaireDue}` : ''}</li>}
                  {draft.uploads.trim() && <li>{draft.uploads.trim().split('\n').length} requested upload(s){draft.uploadsDue ? ` — due ${draft.uploadsDue}` : ''}</li>}
                </ul>
                <p className="mt-3 text-[12px] text-brand-muted">
                  Signing the fee agreement opens the client portal and creates a follow-up due within 24 hours.
                  SMS respects recorded permission and quiet hours.
                </p>
              </div>

              {!hasAgreement && <p role="alert" className="text-[13px] text-brand-rose">Choose or upload the reviewed fee agreement before sending.</p>}
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
      {libraryTarget && (
        <FormLibraryDialog
          matterId={matterId}
          documentCategory={libraryTarget === 'agreement' ? 'contract' : 'general'}
          onAttached={attachFilled}
          onClose={() => setLibraryTarget(null)}
        />
      )}
    </div>
  )
}
