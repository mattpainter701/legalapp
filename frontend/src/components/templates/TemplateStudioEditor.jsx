// The Template Studio visual editor: reopen a saved template's retained source
// and place its variables directly on the document. This is the same field
// geometry the intake wizard writes, so a template prepared at upload time can
// be reopened and adjusted for the rest of its life.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Rnd } from 'react-rnd'
import {
  AlignLeft,
  CalendarDays,
  CheckSquare,
  Eraser,
  Loader2,
  Maximize2,
  Minus,
  PenLine,
  Plus,
  Redo2,
  Save,
  Trash2,
  Type,
  Undo2,
} from 'lucide-react'

import { getTemplateBindings } from '../../api'
import DocxDocumentView from './DocxDocumentView'
import WordDocumentPreview from './WordDocumentPreview'
import { resolveWordPageSelection } from './wordPlaceholderMatches'
import WordDeriveDraftAction from './WordDeriveDraftAction'
import WordCleanupAction from './WordCleanupAction'
import { PdfPageCanvas, PdfThumbnail, useTemplatePdfDocument } from './PdfDocumentCanvas'
import {
  MIN_FIELD_SIZE,
  VARIABLE_NAME_PATTERN,
  canvasToOverlayRect,
  clamp,
  createManualField,
  createCoverRegion,
  fieldIdentity,
  geometryToOverlays,
  isPdfFile,
  overlayToCanvasRect,
  placementsFor,
  sourceKind,
} from './pdfFieldGeometry'

const FIELD_TOOLS = [
  { kind: 'text', label: 'Text', icon: Type },
  { kind: 'multiline', label: 'Paragraph', icon: AlignLeft },
  { kind: 'date', label: 'Date', icon: CalendarDays },
  { kind: 'checkbox', label: 'Checkbox', icon: CheckSquare },
  { kind: 'signature', label: 'Signature', icon: PenLine },
]

const FIELD_TYPES = ['text', 'date', 'checkbox', 'signature', 'number', 'currency']

// Operators that need no literal to compare against. The server accepts
// equals/in/not_in too; those need a value input this panel does not have yet.
const UNARY_OPERATORS = ['present', 'absent', 'truthy', 'falsy']

export const schemaFields = (template) => {
  const fields = template?.variable_schema?.fields
  return Array.isArray(fields) ? fields : []
}

export const schemaRegions = (template) => {
  const regions = template?.variable_schema?.regions
  return Array.isArray(regions) ? regions : []
}
export const schemaCoverRegions = (template) => {
  const regions = template?.variable_schema?.cover_regions
  return Array.isArray(regions) ? regions : []
}

/** Merge edited fields back into the template's schema without dropping
 *  server-owned keys such as page geometry, detection metadata, or version. */
export const mergedVariableSchema = (template, fields, regions, coverRegions = []) => ({
  ...(template?.variable_schema && typeof template.variable_schema === 'object'
    ? template.variable_schema
    : {}),
  fields,
  // Regions are authored metadata like fields, so an editor save carries both;
  // omitting the key entirely keeps a template that has none unchanged.
  ...(regions && regions.length ? { regions } : {}),
  ...((coverRegions.length || template?.variable_schema?.cover_regions)
    ? { cover_regions: coverRegions }
    : {}),
})

function ToolbarButton({ icon: Icon, label, onClick, disabled, active }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${active ? 'border-brand-accent bg-brand-accent/10 text-brand-ink' : 'border-brand-line bg-brand-surface-2 text-brand-muted hover:border-brand-accent/60 hover:text-brand-ink'}`}
    >
      <Icon size={14} aria-hidden="true" />
      <span className="hidden sm:inline">{label}</span>
    </button>
  )
}

function PropertyRow({ label, children }) {
  return (
    <label className="block">
      <span className="block text-[11px] font-semibold uppercase tracking-wide text-brand-muted">{label}</span>
      {children}
    </label>
  )
}

/** Suggest an automation key from the text a user selected.
 *  Names must start with a letter and use only [A-Za-z0-9_.-], so anything
 *  else is folded away and a generic key is used when nothing survives. */
export const docxFieldName = (text) => {
  const slug = String(text || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40)
  return /^[a-z]/.test(slug) ? slug : `field_${slug}`.replace(/_+$/, '') || 'field'
}


/** Load the binding catalogue once and group it for the picker.
 *  The catalogue is static server-owned vocabulary, so a failure to load it
 *  degrades to name matching rather than blocking the editor. */
function useBindingCatalogue() {
  const [catalogue, setCatalogue] = useState({ groups: {}, collections: [] })

  useEffect(() => {
    let cancelled = false
    getTemplateBindings()
      .then((loaded) => {
        if (cancelled) return
        const groups = {}
        for (const entry of loaded?.bindings || []) {
          if (!entry?.path) continue
          ;(groups[entry.group || 'Other'] ||= []).push(entry)
        }
        setCatalogue({ groups, collections: loaded?.collections || [] })
      })
      .catch(() => {
        if (!cancelled) setCatalogue({ groups: {}, collections: [] })
      })
    return () => { cancelled = true }
  }, [])

  return catalogue
}


export default function TemplateStudioEditor({ template, source, sourceError, onSave, onDerived }) {
  const [fields, setFields] = useState(() => schemaFields(template))
  const [applicability, setApplicability] = useState(template.variable_schema?.applicability || null)
  const [regions, setRegions] = useState(() => schemaRegions(template))
  const [coverRegions, setCoverRegions] = useState(() => schemaCoverRegions(template))
  const [sourceReview, setSourceReview] = useState(template.variable_schema?.source_review || {})
  const [cleanupSelection, setCleanupSelection] = useState(null)
  const [wordParagraphs, setWordParagraphs] = useState([])
  const [sourceModeSuggestion, setSourceModeSuggestion] = useState(
    template.variable_schema?.source_mode_suggestion || null,
  )
  const [selectedIdentity, setSelectedIdentity] = useState(
    () => fieldIdentity(schemaFields(template)[0], 0),
  )
  const [pageNumber, setPageNumber] = useState(1)
  const [zoom, setZoom] = useState(0.9)
  const [viewport, setViewport] = useState(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [fieldSearch, setFieldSearch] = useState('')
  const [savedAt, setSavedAt] = useState(null)
  const [renderError, setRenderError] = useState('')
  const undoStack = useRef([])
  const redoStack = useRef([])
  const [historyVersion, setHistoryVersion] = useState(0)
  const scrollerRef = useRef(null)

  const { groups: bindingGroups, collections } = useBindingCatalogue()

  const scenarioBinding = fields.find(field => field.name === applicability?.field)?.binding
  const scenarioDefinition = Object.values(bindingGroups).flat().find(entry => entry.path === scenarioBinding)
  const scenarioOptions = scenarioDefinition?.field_type === 'boolean' ? [{ value: 'true', label: 'Yes' }, { value: 'false', label: 'No' }] : (scenarioDefinition?.options || []).map(value => ({ value, label: value }))
  const pdfSource = isPdfFile(source) ? source : null
  const isDocx = String(template?.format || '').toLowerCase() === 'docx'
  const { document: pdfDocument, pages: pdfPages, error: pdfError } = useTemplatePdfDocument(
    pdfSource,
    { enabled: Boolean(pdfSource) },
  )

  // A different template is a different authoring session; never let undo
  // history cross that boundary.
  useEffect(() => {
    const next = schemaFields(template)
    setFields(next)
    setRegions(schemaRegions(template))
    setCoverRegions(schemaCoverRegions(template))
    setSourceReview(template.variable_schema?.source_review || {})
    setApplicability(template.variable_schema?.applicability || null)
    setSelectedIdentity(fieldIdentity(next[0], 0))
    setPageNumber(1)
    setZoom(0.9)
    setDirty(false)
    setSaveError('')
    setSavedAt(null)
    undoStack.current = []
    redoStack.current = []
    setHistoryVersion((value) => value + 1)
  }, [template?.id, template?.variable_schema])

  const signedPages = template?.variable_schema?.pages
  const effectivePages = Array.isArray(signedPages) && signedPages.length ? signedPages : pdfPages
  const mappedPageCount = fields.reduce((maximum, field) => Math.max(
    maximum,
    Number(field?.page) || 0,
    ...placementsFor(field).map((placement) => Number(placement.overlay?.page) || 0),
  ), 0)
  const pageCount = Math.max(1, effectivePages.length, pdfDocument?.numPages || 0, mappedPageCount)
  const page = effectivePages.find((item) => Number(item?.page) === pageNumber)
    || effectivePages[pageNumber - 1]
    || { page: pageNumber, width: 612, height: 792, rotation: 0 }

  useEffect(() => {
    setPageNumber((value) => clamp(value, 1, pageCount))
  }, [pageCount])

  useEffect(() => setViewport(null), [pageNumber, zoom])

  const indexedFields = fields.map((field, index) => ({ field, index, identity: fieldIdentity(field, index) }))
  const selectedEntry = indexedFields.find((entry) => entry.identity === selectedIdentity)
  const selected = selectedEntry?.field || null

  useEffect(() => {
    if (selectedEntry || !indexedFields.length) return
    setSelectedIdentity(indexedFields[0].identity)
  }, [indexedFields, selectedEntry])

  const canvasWidth = viewport?.width
    || (Number(page.rotation || 0) % 180 ? Number(page.height) : Number(page.width)) * zoom
  const canvasHeight = viewport?.height
    || (Number(page.rotation || 0) % 180 ? Number(page.width) : Number(page.height)) * zoom

  const commitFields = useCallback((nextFields) => {
    undoStack.current = [...undoStack.current.slice(-49), { fields, regions, coverRegions, sourceReview }]
    redoStack.current = []
    setHistoryVersion((value) => value + 1)
    setFields(nextFields)
    setDirty(true)
    setSaveError('')
  }, [fields, regions, coverRegions, sourceReview])

  const undo = () => {
    const previous = undoStack.current.at(-1)
    if (!previous) return
    undoStack.current = undoStack.current.slice(0, -1)
    redoStack.current = [...redoStack.current.slice(-49), { fields, regions, coverRegions, sourceReview }]
    setHistoryVersion((value) => value + 1)
    setFields(previous.fields)
    setRegions(previous.regions)
    setCoverRegions(previous.coverRegions || [])
    setSourceReview(previous.sourceReview || {})
    setDirty(true)
  }

  const redo = () => {
    const next = redoStack.current.at(-1)
    if (!next) return
    redoStack.current = redoStack.current.slice(0, -1)
    undoStack.current = [...undoStack.current.slice(-49), { fields, regions, coverRegions, sourceReview }]
    setHistoryVersion((value) => value + 1)
    setFields(next.fields)
    setRegions(next.regions)
    setCoverRegions(next.coverRegions || [])
    setSourceReview(next.sourceReview || {})
    setDirty(true)
  }

  const updateField = (identity, patch) => {
    commitFields(indexedFields.map((entry) => (
      entry.identity === identity ? { ...entry.field, ...patch } : entry.field
    )))
  }

  const addField = (kind) => {
    const field = createManualField(kind, { page, pageNumber, fields })
    commitFields([...fields, field])
    setSelectedIdentity(field.pdf_source_key)
  }

  const setCoverSource = (entry, enabled) => updateField(entry.identity, {
    erase_source: enabled,
    pdf_overlay: entry.field.pdf_overlay ? { ...entry.field.pdf_overlay, erase_source: enabled } : entry.field.pdf_overlay,
    pdf_overlays: Array.isArray(entry.field.pdf_overlays)
      ? entry.field.pdf_overlays.map((overlay) => ({ ...overlay, erase_source: enabled }))
      : entry.field.pdf_overlays,
  })

  const addCoverRegion = () => {
    const region = { ...createCoverRegion({ page, pageNumber }), id: globalThis.crypto?.randomUUID?.() }
    commitCoverRegions([...coverRegions, region])
  }

  const updateCoverRegion = (index, geometry) => {
    const rect = canvasToOverlayRect(geometry, page, pdfSource ? viewport : null, pdfSource ? 1 : zoom)
    commitCoverRegions(coverRegions.map((item, itemIndex) => itemIndex === index ? { ...item, page: pageNumber, rect } : item))
  }

  const removeCoverRegion = (index) => {
    commitCoverRegions(coverRegions.filter((_, itemIndex) => itemIndex !== index))
  }

  const commitCoverRegions = (nextCoverRegions) => {
    undoStack.current = [...undoStack.current.slice(-49), { fields, regions, coverRegions, sourceReview }]
    redoStack.current = []
    setHistoryVersion((value) => value + 1)
    setCoverRegions(nextCoverRegions)
    setDirty(true)
    setSaveError('')
  }

  // A Word field is created from a text selection rather than a drawn box: the
  // span the user highlighted *is* the anchor, and the exact text it covers is
  // what the renderer re-checks before replacing it.
  const addDocxField = ({ ordinal, start, end, text, label, field_type = 'text', name: requestedName }) => {
    const taken = new Set(fields.map((entry) => entry.name))
    if (requestedName && taken.has(requestedName)) return 'That automation key is already used. Choose another.'
    let name = requestedName || docxFieldName(label || text)
    let suffix = 1
    while (taken.has(name)) {
      suffix += 1
      name = `${docxFieldName(label || text)}_${suffix}`
    }
    const field = {
      name,
      label: label || text.trim().slice(0, 60) || name,
      field_type,
      required: false,
      included: true,
      source_text: text,
      example: text,
      docx_anchor: { paragraph_ordinal: ordinal, start, end },
    }
    commitFields([...fields, field])
    setSelectedIdentity(fieldIdentity(field, fields.length))
  }

  const commitRegions = (nextRegions) => {
    undoStack.current = [...undoStack.current.slice(-49), { fields, regions, sourceReview }]
    redoStack.current = []
    setHistoryVersion((value) => value + 1)
    setRegions(nextRegions)
    setDirty(true)
    setSaveError('')
  }

  const addRegion = (region) => {
    const duplicate = regions.some((entry) => (
      entry.kind === region.kind
      && entry.name === region.name
      && entry.from_ordinal === region.from_ordinal
      && entry.to_ordinal === region.to_ordinal
    ))
    if (duplicate) return
    commitRegions([...regions, region])
  }

  const removeRegion = (region) => {
    commitRegions(regions.filter((entry) => !(
      entry.kind === region.keyword
      && entry.name === region.name
      && entry.from_ordinal === region.from
      && entry.to_ordinal === region.to
    )))
  }

  const removeField = (entry) => {
    // An AcroForm or detected field still exists in the document, so it is
    // excluded rather than deleted; only manual placements are truly removable.
    if (sourceKind(entry.field) === 'manual') {
      const remaining = indexedFields.filter((item) => item.identity !== entry.identity)
      commitFields(remaining.map((item) => item.field))
      setSelectedIdentity(remaining[0]?.identity || '')
    } else {
      updateField(entry.identity, { included: false })
    }
  }

  const updateGeometry = (entry, placementIndex, geometry) => {
    if (entry.field.pdf_field_name) return
    const overlays = geometryToOverlays(entry.field, placementIndex, geometry, {
      page,
      pageNumber,
      viewport: pdfSource ? viewport : null,
      scale: pdfSource ? 1 : zoom,
      canvasWidth,
      canvasHeight,
    })
    updateField(entry.identity, {
      page: Number(overlays[0]?.page) || pageNumber,
      rect: overlays[0]?.rect,
      pdf_overlay: overlays[0],
      pdf_overlays: overlays,
    })
  }

  const fitWidth = useCallback(() => {
    const available = Math.max(240, (scrollerRef.current?.clientWidth || 680) - 40)
    const rotated = Number(page.rotation || 0) % 180 !== 0
    const sourceWidth = rotated ? Number(page.height || 792) : Number(page.width || 612)
    setZoom(clamp(available / sourceWidth, 0.35, 2.5))
  }, [page])

  const duplicateNames = useMemo(() => new Set(fields.filter((field, index) => (
    fields.findIndex((candidate) => candidate.name === field.name) !== index
  )).map((field) => field.name)), [fields])

  const invalidNames = fields.filter((field) => !VARIABLE_NAME_PATTERN.test(field.name || ''))
  const canSave = dirty && !saving && !duplicateNames.size && !invalidNames.length

  const save = async () => {
    if (!canSave) return
    setSaving(true)
    setSaveError('')
    try {
      await onSave({ ...mergedVariableSchema(template, fields, regions, coverRegions), ...(isDocx && template.variable_schema?.source_review_version === 1 ? { source_review: sourceReview } : {}), ...(applicability || template.variable_schema?.applicability ? { applicability } : {}) })
      setDirty(false)
      setSavedAt(new Date())
    } catch (error) {
      setSaveError(error?.message || 'The template could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  // Guard an accidental tab close while placements are unsaved.
  useEffect(() => {
    if (!dirty) return undefined
    const warn = (event) => {
      event.preventDefault()
      event.returnValue = ''
    }
    globalThis.addEventListener?.('beforeunload', warn)
    return () => globalThis.removeEventListener?.('beforeunload', warn)
  }, [dirty])

  const visiblePlacements = indexedFields.flatMap((entry) => (
    placementsFor(entry.field)
      .filter((placement) => Number(placement.overlay?.page || entry.field.page || 1) === pageNumber)
      .filter(() => entry.field.included !== false)
      .map((placement) => ({ ...placement, entry }))
  ))

  const previewProblem = sourceError || pdfError || renderError
  const deriveSchema = {
    ...mergedVariableSchema(template, fields, regions),
    ...(isDocx && template.variable_schema?.source_review_version === 1 ? { source_review: sourceReview } : {}),
    ...(applicability || template.variable_schema?.applicability ? { applicability } : {}),
  }

  return (
    <div className="overflow-hidden rounded-xl border border-brand-line bg-brand-surface-2">
      <div className="border-b border-brand-line p-3 text-sm">
        <p className="font-semibold">{fields.filter(field => field.included !== false).length} included fields · {fields.filter(field => field.included !== false && (field.review_required || field.ai_suggested || Number(field.confidence ?? 1) < 0.75)).length} need review</p>
        <p className="mt-1 text-xs text-brand-muted">Select a named box or a field in the list to edit it. {isDocx ? 'Drag across the words that should change to create a field directly on the page.' : 'Choose a field type in the toolbar to add a box, then move and resize it on the page.'}</p>
      </div>
      {isDocx && template.variable_schema?.source_review_version === 1 && (
        <div className="border-b border-brand-line p-3">
          <WordDeriveDraftAction
            templateId={template.id}
            fields={deriveSchema.fields}
            sourceReview={deriveSchema.source_review || {}}
            reviewedSchema={deriveSchema}
            suggestedMode={sourceModeSuggestion?.suggested_mode || 'prose'}
            onCreated={onDerived}
          />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 border-b border-brand-line px-3 py-2">
        {/* Placement tools need page geometry, so they are PDF-only. Everything
            else about a field — its name, what it fills from, when it applies —
            is editable for every format. */}
        {pdfSource ? (
          <>
            <span className="text-[11px] font-semibold uppercase tracking-wide text-brand-muted">Add field</span>
            {FIELD_TOOLS.map((tool) => (
              <ToolbarButton
                key={tool.kind}
                icon={tool.icon}
                label={tool.label}
                onClick={() => addField(tool.kind)}
              />
            ))}
            <ToolbarButton icon={Eraser} label="Cover" onClick={addCoverRegion} />
            <span className="mx-1 hidden h-5 w-px bg-brand-line sm:block" aria-hidden="true" />
            <ToolbarButton icon={Undo2} label="Undo" onClick={undo} disabled={!undoStack.current.length} />
            <ToolbarButton icon={Redo2} label="Redo" onClick={redo} disabled={!redoStack.current.length} />
            <span className="mx-1 hidden h-5 w-px bg-brand-line sm:block" aria-hidden="true" />
            <ToolbarButton
              icon={Minus}
              label="Zoom out"
              onClick={() => setZoom((value) => clamp(value - 0.15, 0.35, 2.5))}
            />
            <span className="min-w-12 text-center text-xs font-semibold text-brand-ink" aria-live="polite">
              {Math.round(zoom * 100)}%
            </span>
            <ToolbarButton
              icon={Plus}
              label="Zoom in"
              onClick={() => setZoom((value) => clamp(value + 0.15, 0.35, 2.5))}
            />
            <ToolbarButton icon={Maximize2} label="Fit width" onClick={fitWidth} />
          </>
        ) : (
          <>
            <span className="text-[11px] font-semibold uppercase tracking-wide text-brand-muted">Fields</span>
            <ToolbarButton icon={Undo2} label="Undo" onClick={undo} disabled={!undoStack.current.length} />
            <ToolbarButton icon={Redo2} label="Redo" onClick={redo} disabled={!redoStack.current.length} />
          </>
        )}
        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs text-brand-muted" role="status" aria-label="Field save status">
            {saving
              ? 'Saving…'
              : dirty
                ? 'Unsaved changes'
                : savedAt
                  ? `Saved ${savedAt.toLocaleTimeString()}`
                  : 'No changes'}
          </span>
          <button
            type="button"
            onClick={save}
            disabled={!canSave}
            className="inline-flex items-center gap-2 rounded-lg bg-brand-ink px-3.5 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {saving
              ? <Loader2 size={15} className="animate-spin" aria-hidden="true" />
              : <Save size={15} aria-hidden="true" />}
            Save fields
          </button>
        </div>
      </div>

      <details className="border-b border-brand-line p-3 text-sm">
        <summary className="cursor-pointer font-semibold">When to use this template{applicability?.label ? `: ${applicability.label}` : ''}</summary>
        <p className="mt-2 text-xs text-brand-muted">Name the scenario, such as Divorce with children. Generation checks a saved matter or client detail; missing values block selection. This does not create a list of children.</p>
        <label className="mt-2 flex gap-2"><input type="checkbox" checked={Boolean(applicability)} onChange={event => { setApplicability(event.target.checked ? { label: '', field: '', value: '' } : null); setDirty(true) }} />Use only for a matching scenario</label>
        {applicability && <div className="mt-2 grid gap-2 sm:grid-cols-3">
          <label>Scenario label<input aria-label="Scenario label" value={applicability.label} onChange={event => { setApplicability({ ...applicability, label: event.target.value }); setDirty(true) }} className="block w-full border rounded p-2 bg-brand-bg text-brand-ink" /></label>
          <label>Saved detail<select aria-label="Scenario detail" value={applicability.field} onChange={event => { setApplicability({ ...applicability, field: event.target.value }); setDirty(true) }} className="block w-full border rounded p-2 bg-brand-bg text-brand-ink"><option value="">Choose a linked detail</option>{fields.filter(field => field.binding?.startsWith('custom.')).map(field => <option key={field.name} value={field.name}>{field.label || field.name}</option>)}</select></label>
          <label>Required answer{scenarioOptions.length ? <select aria-label="Scenario required answer" value={applicability.value} onChange={event => { setApplicability({ ...applicability, value: event.target.value }); setDirty(true) }} className="block w-full border rounded p-2 bg-brand-bg text-brand-ink"><option value="">Choose an answer</option>{scenarioOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select> : <input aria-label="Scenario required answer" type={['number', 'date'].includes(scenarioDefinition?.field_type) ? scenarioDefinition.field_type : 'text'} placeholder="Answer required for this scenario" value={applicability.value} onChange={event => { setApplicability({ ...applicability, value: event.target.value }); setDirty(true) }} className="block w-full border rounded p-2 bg-brand-bg text-brand-ink" />}</label>
        </div>}
      </details>
      {(saveError || duplicateNames.size > 0 || invalidNames.length > 0) && (
        <div role="alert" className="border-b border-brand-line bg-brand-amber/10 px-4 py-2 text-sm text-brand-ink">
          {saveError
            || (duplicateNames.size
              ? `Duplicate variable name: ${[...duplicateNames].join(', ')}. Names must be unique before saving.`
              : `Invalid variable name: ${invalidNames.map((field) => field.name || '(empty)').join(', ')}. Use letters, digits, dot, dash or underscore, starting with a letter.`)}
        </div>
      )}

      <div className={`grid gap-0 ${pdfSource ? 'lg:grid-cols-[168px_minmax(0,1fr)_288px]' : 'lg:grid-cols-[minmax(0,1fr)_288px]'}`}>
        {!pdfSource && isDocx && (
          <WordDocumentPreview key={`${template.id}:${template.source_sha256 || ''}`} templateId={template.id} sourceDigest={template.source_sha256} fields={fields} paragraphs={wordParagraphs} selectedIdentity={selectedIdentity} onSelectField={setSelectedIdentity}
            onCreateField={({ text, ...options }) => {
              const selection = resolveWordPageSelection(text, wordParagraphs)
              if (!selection) return 'This text occurs more than once or cannot be matched to the Word source. Use Fields to select its exact paragraph.'
              if (fields.some(field => field.included !== false && field.docx_anchor?.paragraph_ordinal === selection.ordinal && field.docx_anchor.start < selection.end && selection.start < field.docx_anchor.end)) return 'This selection overlaps an existing field. Click its box to edit it.'
              return addDocxField({ ...selection, ...options })
            }}
            onUpdateField={(identity, changes) => {
              const entry = indexedFields.find(item => item.identity === identity)
              if (!entry) return 'This field has changed. Select it again.'
              if (changes.name && indexedFields.some(item => item.identity !== identity && item.field.name === changes.name)) return 'That automation key is already used. Choose another.'
              const next = fields.map((field, index) => index === entry.index ? { ...field, ...changes } : field)
              commitFields(next)
              setSelectedIdentity(fieldIdentity(next[entry.index], entry.index))
            }}>
          <DocxDocumentView
            templateId={template.id}
            fields={fields}
            regions={regions}
            sourceReview={sourceReview}
            onSelectText={(selection) => setCleanupSelection({ paragraph_ordinal: selection.ordinal, start: selection.start, end: selection.end, original_text: selection.text })}
            onModeSuggestion={setSourceModeSuggestion}
            onParagraphs={setWordParagraphs}
            onReviewChange={template.variable_schema?.source_review_version === 1 ? (next) => {
              undoStack.current = [...undoStack.current.slice(-49), { fields, regions, sourceReview }]
              redoStack.current = []
              setHistoryVersion(value => value + 1)
              setSourceReview(next)
              setDirty(true)
              setSaveError('')
            } : undefined}
            selectedName={selected?.name}
            collections={collections}
            conditionFields={fields.map((entry) => entry.name).filter(Boolean)}
            onSelectField={(field) => {
              const match = indexedFields.find((entry) => entry.field.name === field.name)
              if (match) setSelectedIdentity(match.identity)
            }}
            onCreateField={addDocxField}
            onCreateRegion={addRegion}
            onRemoveRegion={removeRegion}
          />
          </WordDocumentPreview>
        )}
        {!pdfSource && !isDocx && (
          <div className="max-h-[70vh] overflow-y-auto p-5">
            <h2 className="font-semibold text-brand-ink">Markdown template</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-brand-muted">
              {sourceError
                || 'Edit this template\u2019s wording from Edit template. Field names, data sources, and conditions are editable here.'}
            </p>
            <h3 className="mt-5 text-sm font-semibold text-brand-ink">Conditional and repeating sections</h3>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-brand-muted">
              Write these markers in the template body itself, each one alone on its own line.
            </p>
            <dl className="mt-3 max-w-2xl space-y-2 text-sm">
              {[
                ['{{#if field}} … {{/if}}', 'Include the clause only when that field has a value.'],
                ['{{#unless field}} … {{/unless}}', 'Include it only when the field is empty.'],
                ['{{#each parties}} … {{/each}}', 'Repeat the block once per matter party. Inside it, use {{party_name}} and {{party_role}}.'],
              ].map(([marker, meaning]) => (
                <div key={marker} className="rounded-lg border border-brand-line bg-brand-bg px-3 py-2">
                  <dt className="font-mono text-xs text-brand-ink">{marker}</dt>
                  <dd className="mt-1 text-xs leading-5 text-brand-muted">{meaning}</dd>
                </div>
              ))}
            </dl>
          </div>
        )}
        {pdfSource && (
        <>
        <nav aria-label="Pages" className="hidden max-h-[70vh] space-y-2 overflow-y-auto border-r border-brand-line p-2 lg:block">
          {Array.from({ length: pageCount }, (_, index) => index + 1).map((number) => (
            <PdfThumbnail
              key={number}
              document={pdfDocument}
              pageNumber={number}
              active={number === pageNumber}
              onSelect={() => setPageNumber(number)}
            />
          ))}
        </nav>

        <div ref={scrollerRef} className="max-h-[70vh] overflow-auto bg-brand-bg p-4">
          {previewProblem && (
            <p role="alert" className="mb-3 rounded-lg border border-brand-amber/40 bg-brand-amber/10 px-3 py-2 text-sm text-brand-ink">
              {previewProblem}
            </p>
          )}
          <div
            className="relative mx-auto"
            style={{ width: canvasWidth || 612, height: canvasHeight || 792 }}
          >
            <PdfPageCanvas
              document={pdfDocument}
              pageNumber={pageNumber}
              zoom={zoom}
              onViewport={setViewport}
              onError={(error) => setRenderError(
                `Page ${pageNumber} could not be rendered. (${error?.message || 'Preview unavailable'})`,
              )}
            />
            {coverRegions.map((region, index) => Number(region.page) === pageNumber ? (() => {
              const geometry = overlayToCanvasRect(region, page, pdfSource ? viewport : null, pdfSource ? 1 : zoom)
              return <Rnd key={`cover:${index}`} bounds="parent" size={{ width: geometry.width, height: geometry.height }} position={{ x: geometry.x, y: geometry.y }} minWidth={MIN_FIELD_SIZE} minHeight={MIN_FIELD_SIZE} onDragStop={(_, data) => updateCoverRegion(index, { ...geometry, x: data.x, y: data.y })} onResizeStop={(_, __, ref, ___, position) => updateCoverRegion(index, { x: position.x, y: position.y, width: ref.offsetWidth, height: ref.offsetHeight })} className="rounded-sm border-2 border-slate-700 bg-white/90 cursor-move"><span className="pointer-events-none text-[10px] font-semibold text-slate-700">Cover</span><button type="button" aria-label="Remove cover region" onClick={(event) => { event.stopPropagation(); removeCoverRegion(index) }} className="absolute right-0 top-0 bg-slate-700 px-1 text-[10px] text-white">×</button></Rnd>
            })() : null)}
            {visiblePlacements.map(({ entry, overlay, index }) => {
              const rect = overlayToCanvasRect(
                overlay,
                page,
                pdfSource ? viewport : null,
                pdfSource ? 1 : zoom,
              )
              const active = entry.identity === selectedIdentity
              const locked = Boolean(entry.field.pdf_field_name)
              return (
                <Rnd
                  key={`${entry.identity}:${index}`}
                  size={{ width: rect.width, height: rect.height }}
                  position={{ x: rect.x, y: rect.y }}
                  bounds="parent"
                  minWidth={MIN_FIELD_SIZE}
                  minHeight={MIN_FIELD_SIZE}
                  disableDragging={locked}
                  enableResizing={!locked}
                  onDragStop={(_event, data) => updateGeometry(entry, index, { ...rect, x: data.x, y: data.y })}
                  onResizeStop={(_event, _direction, ref, _delta, position) => updateGeometry(entry, index, {
                    x: position.x,
                    y: position.y,
                    width: ref.offsetWidth,
                    height: ref.offsetHeight,
                  })}
                  onMouseDown={() => setSelectedIdentity(entry.identity)}
                  className={`group rounded-sm border-2 ${active ? 'border-brand-accent bg-brand-accent/20' : 'border-brand-accent-2/70 bg-brand-accent-2/10'} ${locked ? 'cursor-not-allowed' : 'cursor-move'}`}
                >
                  <span className="pointer-events-none block max-w-full truncate rounded-sm bg-brand-ink px-1.5 py-0.5 text-[10px] font-semibold text-white">
                    {entry.field.label || entry.field.name}
                  </span>
                </Rnd>
              )
            })}
          </div>
        </div>

        </>
        )}

        <aside aria-label="Field properties" className="max-h-[70vh] overflow-y-auto border-t border-brand-line p-3 lg:border-l lg:border-t-0">
          <h2 className="text-sm font-semibold text-brand-ink">
            Fields <span className="font-normal text-brand-muted">({fields.filter((field) => field.included !== false).length})</span>
          </h2>
          <input type="search" aria-label="Find a field" placeholder="Find a field…" value={fieldSearch} onChange={event => setFieldSearch(event.target.value)} className="mt-2 w-full rounded border border-brand-line bg-brand-bg p-2 text-sm" />
          <ul className="mt-2 max-h-52 space-y-1 overflow-y-auto">
            {indexedFields.filter(entry => `${entry.field.label || ''} ${entry.field.name || ''} ${entry.field.source_text || ''}`.toLowerCase().includes(fieldSearch.toLowerCase())).map((entry) => (
              <li key={entry.identity}>
                <button
                  type="button"
                  aria-label={entry.field.label || entry.field.name}
                  onClick={() => {
                    setSelectedIdentity(entry.identity)
                    const first = placementsFor(entry.field)[0]?.overlay?.page || entry.field.page
                    if (first) setPageNumber(clamp(Number(first), 1, pageCount))
                  }}
                  aria-current={entry.identity === selectedIdentity ? 'true' : undefined}
                  className={`w-full truncate rounded-md px-2 py-1.5 text-left text-xs ${entry.identity === selectedIdentity ? 'bg-brand-accent/15 font-semibold text-brand-ink' : 'text-brand-muted hover:bg-brand-bg'} ${entry.field.included === false ? 'line-through opacity-60' : ''}`}
                >
                  {entry.field.label || entry.field.name}
                  <span className="block truncate text-[11px] font-normal">{entry.field.included === false ? 'Excluded' : entry.field.review_required || entry.field.ai_suggested || Number(entry.field.confidence ?? 1) < 0.75 ? 'Needs review' : 'Included'} · {entry.field.field_type || 'text'}</span>
                  {entry.field.source_text && <span className="block truncate text-[11px] font-normal">Replaces: {entry.field.source_text}</span>}
                </button>
              </li>
            ))}
            {!indexedFields.length && (
              <li className="rounded-md border border-dashed border-brand-line px-2 py-4 text-center text-xs text-brand-muted">
                No fields yet. Add one from the toolbar.
              </li>
            )}
          </ul>

          {selected ? (
            <div className="mt-4 space-y-3 border-t border-brand-line pt-3">
              <details><summary className="cursor-pointer text-xs text-brand-muted">Advanced: internal field name</summary>
              <PropertyRow label="Variable name">
                <input
                  value={selected.name || ''}
                  onChange={(event) => updateField(selectedEntry.identity, { name: event.target.value })}
                  className="mt-1 w-full rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-sm text-brand-ink"
                />
              </PropertyRow>
              </details>
              <PropertyRow label="Label">
                <input
                  value={selected.label || ''}
                  onChange={(event) => updateField(selectedEntry.identity, { label: event.target.value })}
                  className="mt-1 w-full rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-sm text-brand-ink"
                />
              </PropertyRow>
              {selected.context && <p className="text-xs text-brand-muted">Source context: {selected.context}</p>}
              {isDocx && !selected.docx_choice && (!selected.binding?.startsWith('item.') || selected.value_from) && <PropertyRow label="Use the same value as">
                <select value={selected.value_from || ''} onChange={event => updateField(selectedEntry.identity, { value_from: event.target.value })} className="mt-1 w-full rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-sm">
                  <option value="">Separate value</option>
                  {fields.filter(field => field.name !== selected.name && field.included !== false && !field.value_from && !field.docx_choice && !field.binding?.startsWith('item.') && (field.field_type || 'text') === (selected.field_type || 'text')).map(field => <option key={field.name} value={field.name}>{field.label || field.name}</option>)}
                </select>
              </PropertyRow>}
              <PropertyRow label="Type">
                <select
                  disabled={Boolean(selected.docx_choice)}
                  value={selected.field_type || selected.type || 'text'}
                  onChange={(event) => updateField(selectedEntry.identity, { field_type: event.target.value })}
                  className="mt-1 w-full rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-sm text-brand-ink"
                >
                  {FIELD_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}
                </select>
              </PropertyRow>
              {['signature', 'date', 'initials'].includes(String(selected.field_type || '').toLowerCase()) && (
                <PropertyRow label="Signer role">
                  <input
                    aria-label="Signer role"
                    value={selected.signer_role || ''}
                    placeholder="e.g. client or attorney"
                    onChange={(event) => updateField(selectedEntry.identity, { signer_role: event.target.value.trim() || undefined })}
                    className="mt-1 w-full rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-sm text-brand-ink"
                  />
                  <span className="mt-1 block text-[11px] text-brand-muted">Assign the matching role to the signer when sending. Word fields require position review after generation. Leave ordinary filled dates without a signer role.</span>
                  {selected.field_type === 'signature' && <select aria-label="Signing field kind" value={selected.signing_type || 'signature'} onChange={event => updateField(selectedEntry.identity, { signing_type: event.target.value })} className="mt-2 w-full rounded border border-brand-line p-2 text-sm"><option value="signature">Signature</option><option value="initials">Initials</option></select>}
                </PropertyRow>
              )}
              <PropertyRow label="Fills from">
                <select
                  value={selected.binding || ''}
                  onChange={(event) => updateField(selectedEntry.identity, { binding: event.target.value || undefined })}
                  className="mt-1 w-full rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-sm text-brand-ink"
                >
                  <option value="">Match by field name</option>
                  <option value="manual">Always typed by hand</option>
                  {Object.entries(bindingGroups).map(([group, entries]) => (
                    <optgroup key={group} label={group}>
                      {entries.map((entry) => (
                        <option key={entry.path} value={entry.path}>{entry.label}</option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </PropertyRow>
              <p className="text-[11px] leading-4 text-brand-muted">
                {selected.binding && selected.binding !== 'manual'
                  ? 'This field fills from the matter every time, whatever it is named.'
                  : selected.binding === 'manual'
                    ? 'Never filled automatically.'
                    : 'Filled only when the field name happens to match a known record.'}
              </p>
              <PropertyRow label="Only include when">
                <div className="mt-1 flex gap-1.5">
                  <select
                    value={selected.logic?.field || ''}
                    onChange={(event) => updateField(selectedEntry.identity, {
                      logic: event.target.value
                        ? { ...(selected.logic || { operator: 'present' }), field: event.target.value }
                        : undefined,
                    })}
                    className="min-w-0 flex-1 rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-sm text-brand-ink"
                  >
                    <option value="">Always include</option>
                    {fields
                      .filter((entry) => entry?.name && entry.name !== selected.name)
                      .map((entry) => (
                        <option key={entry.name} value={entry.name}>{entry.label || entry.name}</option>
                      ))}
                  </select>
                  {selected.logic?.field && (
                    <select
                      value={selected.logic?.operator || 'present'}
                      onChange={(event) => updateField(selectedEntry.identity, {
                        // Unary operators carry no value; dropping it keeps the
                        // stored condition valid whichever way the user switches.
                        logic: { field: selected.logic.field, operator: event.target.value },
                      })}
                      className="w-28 shrink-0 rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-sm text-brand-ink"
                      aria-label="Condition"
                    >
                      {UNARY_OPERATORS.map((operator) => (
                        <option key={operator} value={operator}>{operator}</option>
                      ))}
                    </select>
                  )}
                </div>
              </PropertyRow>
              <label className="flex items-center gap-2 text-sm text-brand-ink">
                <input
                  type="checkbox"
                  checked={Boolean(selected.required)}
                  onChange={(event) => updateField(selectedEntry.identity, { required: event.target.checked })}
                />
                Required
              </label>
              {placementsFor(selected).length > 0 && <label className="flex items-start gap-2 text-sm text-brand-ink"><input type="checkbox" aria-label="Cover what is underneath" checked={Boolean(selected.erase_source ?? placementsFor(selected)[0]?.overlay?.erase_source)} onChange={(event) => setCoverSource(selectedEntry, event.target.checked)} className="mt-0.5" /><span>Cover what is underneath<p className="text-[11px] text-brand-muted">Paints white over the source when generated.</p></span></label>}
              <p className="text-[11px] text-brand-muted">
                Page {Number(placementsFor(selected)[0]?.overlay?.page || selected.page || 1)}
                {selected.pdf_field_name ? ' · AcroForm field (position fixed by the document)' : ''}
              </p>
              <button
                type="button"
                onClick={() => removeField(selectedEntry)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-brand-line px-2.5 py-1.5 text-xs font-semibold text-brand-muted hover:border-red-400 hover:text-red-500"
              >
                <Trash2 size={14} aria-hidden="true" />
                {sourceKind(selected) === 'manual' ? 'Delete field' : 'Exclude field'}
              </button>
            </div>
          ) : (
            <p className="mt-4 border-t border-brand-line pt-3 text-xs text-brand-muted">
              Select a field on the page to edit its properties.
            </p>
          )}
          <span className="sr-only" aria-live="polite">History step {historyVersion}</span>
        </aside>
      </div>
      {isDocx && <WordCleanupAction templateId={template.id} selection={cleanupSelection} onCreated={onDerived} />}
    </div>
  )
}
