import { useEffect, useRef } from 'react'
import {
  ArrowRight,
  Check,
  FileCheck2,
  FileText,
  FolderKanban,
  LayoutTemplate,
  Loader2,
  Save,
  Sparkles,
  X,
} from 'lucide-react'
import { API_BASE_URL } from '../../api'

const sourceHref = (url) => {
  const value = String(url || '').trim()
  if (value.startsWith('/api/')) {
    return API_BASE_URL === '/api' ? value : `${API_BASE_URL}${value.slice('/api'.length)}`
  }
  return /^https?:\/\//i.test(value) ? value : ''
}

function WorkflowStep({ number, complete, current, label, detail }) {
  return (
    <li className="flex min-w-0 flex-1 items-center gap-2">
      <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-bold ${complete ? 'border-emerald-600 bg-emerald-600 text-white' : current ? 'border-brand-accent bg-brand-accent text-white ring-4 ring-brand-accent/10' : 'border-brand-line bg-white text-brand-muted'}`}>
        {complete ? <Check size={13} /> : number}
      </span>
      <span className="min-w-0">
        <span className={`block truncate text-xs font-bold ${current ? 'text-brand-ink' : 'text-brand-muted'}`}>{label}</span>
        <span className="hidden truncate text-[10px] text-brand-muted sm:block">{detail}</span>
      </span>
    </li>
  )
}

export default function DocumentDraftWorkspace({
  open,
  title,
  body,
  matterLabel = 'Linked matter',
  templateName,
  sources = [],
  dirty = false,
  saving = false,
  approving = false,
  approved = false,
  officeSnapshot = false,
  previewTruncated = false,
  cloudDocumentUrl,
  downloadUrl,
  storageBackend,
  storageState = 'verified',
  notice,
  error,
  onTitleChange,
  onBodyChange,
  onSave,
  onApprove,
  onClose,
}) {
  const dialogRef = useRef(null)
  const onCloseRef = useRef(onClose)

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    if (!open) return undefined
    const previous = document.activeElement
    const onKeyDown = (event) => {
      if (event.key === 'Escape' && !saving && !approving) onCloseRef.current?.()
    }
    document.addEventListener('keydown', onKeyDown)
    window.setTimeout(() => dialogRef.current?.querySelector('input')?.focus(), 0)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      previous?.focus?.()
    }
  }, [approving, open, saving])

  if (!open) return null
  const textEditable = !approved && !officeSnapshot
  const wordCount = String(body || '').trim() ? String(body).trim().split(/\s+/).length : 0
  const cloudOpenHref = sourceHref(cloudDocumentUrl)
  const downloadHref = sourceHref(downloadUrl)
  const storageLabel = {
    google_drive: 'Google Drive',
    onedrive: 'OneDrive',
    sharepoint: 'SharePoint',
  }[storageBackend] || 'Tenant cloud'
  const statusLabel = storageState === 'conflict'
    ? 'Cloud conflict'
    : approved ? 'Approved' : 'Cloud copy ready'

  return (
    <div className="fixed inset-0 z-[90] flex bg-brand-ink/45 p-0 backdrop-blur-[2px] sm:p-4" role="presentation">
      <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="document-workspace-title" className="m-auto flex h-full w-full max-w-7xl flex-col overflow-hidden bg-brand-bg shadow-2xl sm:h-[94vh] sm:rounded-2xl sm:border sm:border-brand-line">
        <header className="flex shrink-0 items-start gap-3 border-b border-brand-line bg-white px-4 py-3 sm:px-6">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-700"><FileText size={20} /></div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-brand-accent">Document workspace</p>
              <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${storageState === 'conflict' ? 'border-red-200 bg-red-50 text-red-700' : approved ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-violet-200 bg-violet-50 text-violet-700'}`}>{statusLabel}</span>
              {dirty && <span className="text-[11px] font-semibold text-amber-700">Unsaved changes</span>}
            </div>
            <h2 id="document-workspace-title" className="mt-0.5 truncate font-serif text-lg font-bold text-brand-ink sm:text-xl">{title || 'Untitled document'}</h2>
          </div>
          <button type="button" onClick={onClose} disabled={saving || approving} aria-label="Close document workspace" className="rounded-lg p-2 text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink disabled:opacity-50"><X size={19} /></button>
        </header>

        <ol aria-label="Document workflow" className="flex shrink-0 items-center gap-2 border-b border-brand-line bg-white px-4 py-3 sm:px-6">
          <WorkflowStep number="1" complete label="Tenant cloud copy" detail={`${storageLabel} · editable DOCX`} />
          <span className="h-px w-5 shrink-0 bg-brand-line sm:w-10" aria-hidden="true" />
          <WorkflowStep number="2" current={!approved} complete={approved} label="Team review" detail="Staff, then attorney" />
          <span className="h-px w-5 shrink-0 bg-brand-line sm:w-10" aria-hidden="true" />
          <WorkflowStep number="3" complete={approved} current={approved} label="Approved revision" detail="Exact bytes verified" />
        </ol>

        <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_320px]">
          <main className="overflow-y-auto bg-[#e9e7e1] p-3 sm:p-6 lg:p-8">
            <div className="mx-auto mb-3 flex max-w-[760px] items-center justify-between text-[11px] font-semibold text-brand-muted">
              <span>{officeSnapshot ? 'Read-only office snapshot' : 'Editable draft'}</span>
              <span>{wordCount.toLocaleString()} words</span>
            </div>
            <div className="mx-auto min-h-[820px] max-w-[760px] bg-white px-6 py-8 shadow-[0_8px_30px_rgba(37,34,28,0.12)] sm:px-14 sm:py-12">
              <label className="block">
                <span className="sr-only">Document title</span>
                <input value={title} onChange={(event) => onTitleChange?.(event.target.value)} maxLength={300} disabled={!textEditable} className="no-transition w-full border-0 border-b border-transparent bg-transparent px-0 pb-4 font-serif text-2xl font-bold text-brand-ink outline-none hover:border-brand-line focus:border-brand-accent focus:ring-0 disabled:cursor-default sm:text-3xl" />
              </label>
              <label className="mt-7 block">
                <span className="sr-only">Document text</span>
                <textarea value={body} onChange={(event) => onBodyChange?.(event.target.value)} maxLength={officeSnapshot ? 20000 : 50000} disabled={!textEditable} className="no-transition min-h-[650px] w-full resize-none border-0 bg-transparent p-0 font-serif text-[15px] leading-7 text-brand-ink outline-none focus:ring-0 disabled:cursor-default sm:text-base" />
              </label>
            </div>
          </main>

          <aside className="overflow-y-auto border-t border-brand-line bg-white p-4 lg:border-l lg:border-t-0 lg:p-5">
            <section>
              <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-brand-muted"><Sparkles size={13} /> Draft basis</h3>
              <div className="mt-3 space-y-2">
                <div className="rounded-xl border border-brand-line bg-brand-bg-soft p-3">
                  <p className="flex items-center gap-2 text-xs font-bold text-brand-ink"><FolderKanban size={14} className="text-brand-accent" /> {matterLabel}</p>
                  <p className="mt-1 text-[11px] leading-relaxed text-brand-muted">Matter facts, chat instructions, and cited materials stay attached automatically.</p>
                </div>
                <div className="rounded-xl border border-dashed border-brand-line p-3">
                  <p className="flex items-center gap-2 text-xs font-bold text-brand-ink"><LayoutTemplate size={14} /> {templateName || 'No template attached'}</p>
                  <p className="mt-1 text-[11px] leading-relaxed text-brand-muted">When firm templates are available, the selected original will appear here as the drafting foundation.</p>
                </div>
              </div>
            </section>

            {sources.length > 0 && (
              <section className="mt-6">
                <h3 className="text-xs font-bold uppercase tracking-wide text-brand-muted">Supporting material</h3>
                <ul className="mt-2 space-y-1.5">
                  {sources.map((source) => {
                    const href = sourceHref(source.url)
                    const content = <><FileCheck2 size={13} className="shrink-0 text-brand-accent" /><span className="truncate">{source.label}</span></>
                    return <li key={source.source_id}>{href ? <a href={href} target="_blank" rel="noreferrer" className="flex items-center gap-2 rounded-lg border border-brand-line px-2.5 py-2 text-xs font-semibold text-brand-ink hover:border-brand-accent">{content}</a> : <span className="flex items-center gap-2 rounded-lg border border-brand-line px-2.5 py-2 text-xs font-semibold text-brand-muted">{content}</span>}</li>
                  })}
                </ul>
              </section>
            )}

            <section className="mt-6 rounded-xl bg-brand-ink p-4 text-white">
              <h3 className="text-sm font-bold">Tenant-owned working document</h3>
              <p className="mt-1 text-[11px] leading-relaxed text-white/70">{officeSnapshot ? 'This is a read-only office snapshot. Continue editing in Word, LibreOffice, OpenOffice, or the connected cloud editor, then use Refresh edits from cloud to adopt the next exact snapshot.' : `The DOCX already lives in ${storageLabel}. Each LawHand save creates a new verified revision and resets review. Approval records the exact cloud bytes; client delivery is a separate action.`}</p>
              {previewTruncated && <p className="mt-2 rounded-lg border border-amber-300/40 bg-amber-300/10 p-2 text-[10px] font-semibold leading-relaxed text-amber-100">Preview truncated for safe display. Use the cloud editor or download the exact DOCX to review the complete document.</p>}
              {cloudOpenHref && <a href={cloudOpenHref} target="_blank" rel="noreferrer" className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-white px-3 py-2.5 text-sm font-bold text-brand-ink"><FileText size={15} />Open cloud working copy</a>}
              {downloadHref && <a href={downloadHref} className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-lg border border-white/30 px-3 py-2.5 text-sm font-bold text-white hover:bg-white/10"><FileText size={15} />Download exact DOCX</a>}
              <p className="mt-2 text-[10px] leading-relaxed text-white/60">DOCX opens in Microsoft Word, LibreOffice, and OpenOffice. Google Drive can open compatible DOCX files.</p>
              {textEditable && <button type="button" onClick={onSave} disabled={saving || approving || !dirty} className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-white px-3 py-2.5 text-sm font-bold text-brand-ink disabled:cursor-not-allowed disabled:opacity-50">{saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}{saving ? 'Saving new revision…' : dirty ? 'Save as new cloud revision' : 'Cloud revision saved'}</button>}
              {!approved && onApprove && <button type="button" onClick={onApprove} disabled={saving || approving || dirty || storageState !== 'verified'} className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-brand-accent px-3 py-2.5 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-50">{approving ? <Loader2 size={15} className="animate-spin" /> : <ArrowRight size={15} />}{dirty ? 'Save before approval' : storageState === 'conflict' ? 'Reconcile cloud copy' : 'Review exact revision'}</button>}
            </section>
            {notice && <p role="status" className="mt-3 text-xs font-semibold text-emerald-700">{notice}</p>}
            {error && <p role="alert" className="mt-3 text-xs font-semibold text-red-700">{error}</p>}
          </aside>
        </div>
      </section>
    </div>
  )
}
