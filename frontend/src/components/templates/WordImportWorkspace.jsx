import { useEffect, useRef, useState } from 'react'
import { previewWordUpload } from '../../api'
import WordDocumentPreview from './WordDocumentPreview'
import { fieldIdentity } from './pdfFieldGeometry'

export default function WordImportWorkspace({ file, analysis, fields, onFieldsChange, onAddField, reviewConfirmed = false }) {
  const [selected, setSelected] = useState('')
  const [selection, setSelection] = useState('')
  const [query, setQuery] = useState('')
  const [reviewOnly, setReviewOnly] = useState(false)
  const propertiesRef = useRef(null)
  const fieldsRef = useRef(null)
  const documentRef = useRef(null)
  const textRef = useRef(null)
  const previousCount = useRef(fields.length)
  useEffect(() => {
    if (previousCount.current > 0 && fields.length > previousCount.current) setSelected(fieldIdentity(fields.at(-1), fields.length - 1))
    previousCount.current = fields.length
  }, [fields])
  const text = analysis?.extracted_text || analysis?.body || ''
  const included = fields.filter(field => field.included !== false)
  const needsReview = reviewConfirmed ? [] : included.filter(field => field.review_required || field.ai_suggested || Number(field.confidence ?? 1) < 0.75)
  const entries = fields.map((field, index) => ({ field, index, identity: fieldIdentity(field, index) }))
  const active = entries.find(entry => entry.identity === selected) || entries[0]
  const visibleEntries = entries.filter(({ field }) => (!reviewOnly || needsReview.includes(field))
    && `${field.label || ''} ${field.name || ''} ${field.source_text || ''}`.toLowerCase().includes(query.trim().toLowerCase()))
  const sourceParagraph = analysis?.source_paragraphs?.find(paragraph => paragraph.ordinal === active?.field.docx_anchor?.paragraph_ordinal)
  const selectField = identity => {
    setSelected(identity)
    propertiesRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  }
  const nextReview = () => {
    const candidates = entries.filter(entry => needsReview.includes(entry.field))
    const index = candidates.findIndex(entry => entry.identity === active?.identity)
    if (candidates.length) selectField(candidates[(index + 1) % candidates.length].identity)
  }
  const update = changes => {
    const next = fields.map((field, index) => index === active?.index ? { ...field, ...changes } : field)
    if (active) setSelected(fieldIdentity(next[active.index], active.index))
    onFieldsChange(next)
  }
  const updateDocumentField = (identity, changes) => {
    const entry = entries.find(item => item.identity === identity)
    if (!entry) return 'This field has changed. Select it again.'
    if (changes.name && entries.some(item => item.identity !== identity && item.field.name === changes.name)) return 'That automation key is already used. Choose another.'
    const next = fields.map((field, index) => index === entry.index ? { ...field, ...changes } : field)
    setSelected(fieldIdentity(next[entry.index], entry.index))
    onFieldsChange(next)
  }
  const pickText = () => {
    const range = globalThis.getSelection?.()
    if (!range || range.isCollapsed || !textRef.current?.contains(range.anchorNode) || !textRef.current?.contains(range.focusNode)) return
    setSelection(range.toString().trim())
  }
  return <section aria-label="Review Word upload" className="rounded-xl border border-brand-line bg-brand-surface-2">
    <div className="border-b border-brand-line p-4">
      <h2 className="font-semibold">Review your document and fields</h2>
      <p role="status" className="mt-1 text-sm text-brand-muted">{analysis ? `${included.length} detected or added fields · ${needsReview.length} need review` : 'Rendering the document and detecting fields…'}</p>
      <p className="mt-1 text-xs text-brand-muted">Click a named box to edit it. To add a field, drag across the words to replace on the document, then give the field a name. The list also includes fields on other pages or awaiting placement.</p>
      <button type="button" onClick={() => fieldsRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' })} className="mt-2 rounded border border-brand-line px-3 py-2 text-xs font-semibold lg:hidden">Review detected fields ({fields.length})</button>
      {analysis && <div className="mt-3 rounded-lg border border-brand-line bg-brand-bg p-3">
        <p className="text-sm font-semibold">Make this a reusable master</p>
        <ol className="mt-2 grid gap-2 text-xs text-brand-muted sm:grid-cols-3">
          <li><strong className="block text-brand-ink">1. Name the details</strong>Use labels your team understands, such as Client name or Monthly payment.</li>
          <li><strong className="block text-brand-ink">2. Check the whole document</strong>Review every page for unmapped blanks and names or amounts that belong only to the sample.</li>
          <li><strong className="block text-brand-ink">3. Save a draft, then test</strong>Inspect generated output with representative values before publishing for your team.</li>
        </ol>
        <p className="mt-2 text-xs text-brand-muted">Detection is a starting point. A filled field count does not certify that sample details have been removed.</p>
      </div>}
      {analysis?.warnings?.length > 0 && <ul aria-label="Document scan warnings" className="mt-2 list-disc pl-4 text-xs text-brand-muted">{analysis.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>}
    </div>
    <div className="grid min-w-0 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div ref={documentRef} className="min-w-0">
      <WordDocumentPreview file={file} loadUploadPreview={previewWordUpload} fields={fields} paragraphs={analysis?.source_paragraphs} selectedIdentity={active?.identity} onSelectField={setSelected} onUpdateField={updateDocumentField} onCreateField={({ text: source, ...options }) => analysis ? onAddField(source, options) : 'The document scan is still running. Try again when field detection finishes.'} selectionNote="Matching occurrences of this exact text will use the same value.">
        <div className="p-4">
          <p className="mb-3 text-sm font-semibold">Highlight the exact words that should become a field.</p>
          {!analysis ? <p role="status">Reading source text…</p> : <div ref={textRef} onMouseUp={pickText} onKeyUp={pickText} className="max-h-[55vh] select-text overflow-auto whitespace-pre-wrap rounded border border-brand-line bg-white p-5 text-sm leading-7 text-slate-900" aria-label="Select source text">{text || 'No selectable text was found. Try scanning this document again.'}</div>}
          {selection && <div className="sticky bottom-0 mt-3 rounded border border-brand-accent bg-brand-surface-2 p-3">
            <p className="text-sm">Selected: <strong>{selection}</strong></p>
            <p className="mt-1 text-xs text-brand-muted">Matching occurrences of this exact text will use the same value.</p>
            {/[\r\n]/.test(selection) && <p role="alert" className="mt-2 text-sm">Select words within one paragraph to create a field.</p>}
            <button type="button" disabled={/[\r\n]/.test(selection)} onClick={() => { onAddField(selection); setSelection(''); globalThis.getSelection?.()?.removeAllRanges() }} className="mt-2 rounded bg-brand-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">Make selection a field</button>
          </div>}
        </div>
      </WordDocumentPreview>
      </div>
      <aside ref={fieldsRef} className="border-t border-brand-line p-4 lg:border-l lg:border-t-0" aria-label="Detected Word fields">
        <h3 className="font-semibold">Fields found and added ({fields.length})</h3>
        <button type="button" onClick={() => documentRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' })} className="mt-2 rounded border border-brand-line px-3 py-2 text-xs font-semibold lg:hidden">Back to document</button>
        <label className="mt-3 block text-xs font-semibold">Find a detected field<input type="search" value={query} onChange={event => setQuery(event.target.value)} className="mt-1 w-full rounded border border-brand-line bg-brand-bg p-2 text-sm" placeholder="Search labels or source text" /></label>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button type="button" aria-pressed={reviewOnly} onClick={() => setReviewOnly(value => !value)} className="rounded border border-brand-line px-2 py-2 text-xs aria-pressed:bg-brand-accent/10">Needs review ({needsReview.length})</button>
          <button type="button" disabled={!needsReview.length} onClick={nextReview} className="rounded bg-brand-ink px-2 py-2 text-xs font-semibold text-white disabled:opacity-40">Next field to review</button>
        </div>
        {!visibleEntries.length && fields.length > 0 && <p role="status" className="mt-3 text-sm">No fields match this view. <button type="button" onClick={() => { setQuery(''); setReviewOnly(false) }} className="underline">Show all fields</button></p>}
        <ul className="mt-2 max-h-64 space-y-2 overflow-y-auto">
          {visibleEntries.map(entry => <li key={entry.identity}><button type="button" aria-label={`Select ${entry.field.label || entry.field.name}`} aria-pressed={entry.identity === active?.identity} onClick={() => selectField(entry.identity)} className="w-full rounded border border-brand-line p-2 text-left text-sm aria-pressed:border-brand-accent aria-pressed:bg-brand-accent/10">
            <strong className="block">{entry.field.label || entry.field.name}</strong>
            <span className="block truncate text-xs text-brand-muted">{entry.field.source_text || entry.field.example || 'Choose source text'}</span>
            <span className="text-xs">{entry.field.included === false ? 'Excluded' : reviewConfirmed ? 'Reviewed against source' : entry.field.review_required || entry.field.ai_suggested || Number(entry.field.confidence ?? 1) < 0.75 ? 'Needs review' : 'Detected · verify replacement'}</span>
            {entry.field.ai_suggested && <span className="block text-xs">AI proposal · verify</span>}
          </button></li>)}
        </ul>
        {analysis && !fields.length && <p className="mt-3 text-sm text-brand-muted">No fields found automatically. Select words on the document to create your first field.</p>}
        {active && <div ref={propertiesRef} className="mt-4 space-y-3 border-t border-brand-line pt-4">
          <p className="text-xs font-semibold text-brand-muted">Field {active.index + 1} of {fields.length}</p>
          <div className="rounded-lg border border-brand-line bg-brand-bg p-3 text-xs">
            <strong>Source context</strong>
            <p className="mt-1 whitespace-pre-wrap">{sourceParagraph?.text || (active.field.docx_anchor ? 'This field is beyond the available text outline. Use the document page selector to inspect its location before confirming review.' : active.field.source_text || active.field.example || 'Select text on the document to attach this field.')}</p>
          </div>
          <label className="block text-sm">Field label<input aria-label="Imported field label" value={active.field.label ?? active.field.name} onChange={event => update({ label: event.target.value })} className="mt-1 w-full rounded border border-brand-line bg-brand-bg p-2" /></label>
          <label className="block text-sm">Field type<select aria-label="Imported field type" disabled={Boolean(active.field.docx_choice)} value={active.field.field_type || 'text'} onChange={event => update({ field_type: event.target.value })} className="mt-1 w-full rounded border border-brand-line bg-brand-bg p-2">{['text', 'date', 'number', 'currency', 'checkbox', 'signature'].map(type => <option key={type} value={type}>{type}</option>)}</select></label>
          <label className="flex gap-2 text-sm"><input type="checkbox" checked={active.field.included !== false} onChange={event => update({ included: event.target.checked })} />Include this field</label>
          <label className="flex gap-2 text-sm"><input type="checkbox" checked={Boolean(active.field.required)} onChange={event => update({ required: event.target.checked })} />Require a value</label>
          <p className="text-xs text-brand-muted">Replaces: {active.field.source_text || active.field.example}</p>
          {active.field.ai_reason && <p className="text-xs text-brand-muted">{active.field.ai_reason}</p>}
          <details className="text-xs"><summary className="cursor-pointer">Advanced field name</summary><label className="mt-2 block">Automation key<input aria-label="Automation key" value={active.field.name || ''} onChange={event => update({ name: event.target.value })} className="mt-1 w-full rounded border border-brand-line bg-brand-bg p-2" /></label></details>
        </div>}
      </aside>
    </div>
  </section>
}
