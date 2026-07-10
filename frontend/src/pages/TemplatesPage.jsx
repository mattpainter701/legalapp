import React, { useState, useEffect, useCallback, useMemo } from 'react'
import {
  getTemplates,
  analyzeTemplateUpload,
  createTemplate,
  createTemplateFromUpload,
  updateTemplate,
  deleteTemplate,
  renderTemplate,
  renderTemplateFile,
  getMattersV2,
  discoverTemplateVariables,
  getMatterDocumentDownloadUrl,
  triggerBlobDownload,
} from '../api'
import {
  FileText,
  Plus,
  Pencil,
  Trash2,
  X,
  Send,
  Eye,
  Sparkles,
  Check,
  ClipboardList,
  Clock3,
  AlertCircle,
  Search,
  Wand2,
  Upload,
  Download,
} from 'lucide-react'

const CATEGORY_COLORS = {
  engagement_letter: 'bg-blue-100 text-blue-800',
  retainer: 'bg-purple-100 text-purple-800',
  NDA: 'bg-amber-100 text-amber-800',
  motion: 'bg-rose-100 text-rose-800',
  other: 'bg-gray-100 text-gray-800',
}

const CATEGORY_LABELS = {
  engagement_letter: 'Engagement Letter',
  retainer: 'Retainer',
  NDA: 'NDA',
  motion: 'Motion',
  other: 'Other',
}

const TABS = [
  { key: 'templates', label: 'Templates', icon: FileText },
  { key: 'generate', label: 'Generate / Smart Fill', icon: Wand2 },
]

const normalizeItems = (data) => (Array.isArray(data) ? data : (data?.items || []))

const getErrorMessage = (err, fallback) => (
  err?.response?.data?.detail || err?.message || fallback
)

const getTemplateVariables = (template) => {
  const matches = template?.body?.match(/\{\{(.+?)\}\}/g) || []
  const bodyNames = matches.map((m) => m.slice(2, -2).trim()).filter(Boolean)
  const schemaNames = (template?.variable_schema?.fields || []).map((field) => field?.name).filter(Boolean)
  return [...new Set([...bodyNames, ...schemaNames])]
}

const friendlyVariableLabel = (name) => name
  .replace(/[_-]+/g, ' ')
  .replace(/\b\w/g, (c) => c.toUpperCase())

const formatMatterLabel = (matter) => {
  if (!matter) return ''
  const name = matter.matter_name || matter.title || matter.name || 'Untitled matter'
  return [name, matter.client_name, matter.practice_area, matter.status].filter(Boolean).join(' - ')
}

function Modal({ title, children, onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-12">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative bg-brand-surface-2 border border-brand-line rounded-lg shadow-xl w-full max-w-3xl mx-4 max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-brand-line">
          <h2 className="text-lg font-semibold text-brand-ink">{title}</h2>
          <button
            onClick={onClose}
            className="p-1 text-brand-muted hover:text-brand-ink"
            aria-label="Close"
          >
            <X size={20} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
      </div>
    </div>
  )
}

function ConfirmDialog({ message, onConfirm, onCancel }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onCancel} />
      <div className="relative bg-brand-surface-2 border border-brand-line rounded-lg shadow-xl p-6 max-w-sm w-full mx-4">
        <p className="text-brand-ink mb-6">{message}</p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink border border-brand-line rounded"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 text-sm text-white bg-brand-rose hover:bg-red-700 rounded"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

function TemplateForm({ initial, onSubmit, onCancel }) {
  const [title, setTitle] = useState(initial?.title || '')
  const [body, setBody] = useState(initial?.body || '')
  const [category, setCategory] = useState(initial?.category || 'other')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const isPdfTemplate = String(initial?.format || '').toLowerCase() === 'pdf'

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!title.trim() || (!isPdfTemplate && !body.trim())) return
    setSaving(true)
    setError(null)
    try {
      await onSubmit(isPdfTemplate
        ? { title: title.trim(), category }
        : { title: title.trim(), body: body.trim(), category })
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to save template.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-3 py-2">
          {error}
        </div>
      )}
      <div>
        <label htmlFor="template-title" className="block text-sm font-medium text-brand-ink mb-1">
          Title
        </label>
        <input
          id="template-title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
          placeholder="Engagement Letter Template"
          required
        />
      </div>
      <div>
        <label htmlFor="template-category" className="block text-sm font-medium text-brand-ink mb-1">
          Category
        </label>
        <select
          id="template-category"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
        >
          {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      {isPdfTemplate ? (
        <div role="note" className="rounded border border-brand-line bg-brand-bg px-3 py-2">
          <p className="text-sm font-medium text-brand-ink">PDF layout and field mappings come from the source file.</p>
          <p className="mt-1 text-xs text-brand-muted">
            Rename or recategorize this template here. To replace its PDF or mappings, recreate it from Upload Sample and verify the new preview before activation.
          </p>
        </div>
      ) : (
        <div>
          <label htmlFor="template-body" className="block text-sm font-medium text-brand-ink mb-1">
            Body
          </label>
          <textarea
            id="template-body"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent font-mono"
            rows={16}
            placeholder={'Dear {{client_name}},\n\nThis letter confirms...\n\nSincerely,\n{{attorney_name}}'}
            required
          />
          <p className="text-xs text-brand-muted mt-1">
            Use {'{{variable_name}}'} for placeholders.
          </p>
        </div>
      )}
      <div className="flex justify-end gap-3 pt-2">
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink border border-brand-line rounded"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={saving}
          className="px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded disabled:opacity-50"
        >
          {saving ? 'Saving...' : initial ? 'Update' : 'Create'}
        </button>
      </div>
    </form>
  )
}

const isPdfSourceMissing = (template) => (
  String(template?.format || '').toLowerCase() === 'pdf'
  && (!template?.source_filename || !template?.source_sha256)
)

export const replaceTemplateVariable = (body, from, to) => {
  if (!from || !to || from === to) return body
  const escaped = from.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return String(body || '').replace(new RegExp(`\\{\\{\\s*${escaped}\\s*\\}\\}`, 'g'), `{{${to}}}`)
}

export const normalizeVariableName = (value) => String(value || '')
  .trim()
  .toLowerCase()
  .replace(/[^a-z0-9]+/g, '_')
  .replace(/^_+|_+$/g, '')

export const downloadRenderedText = (rendered, title) => {
  const filename = `${String(title || 'generated-document').replace(/[^a-z0-9._-]+/gi, '_')}.md`
  triggerBlobDownload(new Blob([String(rendered || '')], { type: 'text/markdown;charset=utf-8' }), filename)
}

function UploadTemplateForm({ onCreated, onCancel }) {
  const [file, setFile] = useState(null)
  const [title, setTitle] = useState('')
  const [category, setCategory] = useState('other')
  const [analysis, setAnalysis] = useState(null)
  const [draftBody, setDraftBody] = useState('')
  const [mappedFields, setMappedFields] = useState([])
  const [analyzing, setAnalyzing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const reviewedFields = () => mappedFields.map(({ _bodyName, ...field }) => field)

  const reviewedVariableSchema = () => ({
    ...(analysis?.suggested_variable_schema || {}),
    fields: reviewedFields(),
  })

  const buildFormData = ({ includeCategory = false, includeReview = false } = {}) => {
    const form = new FormData()
    form.append('file', file)
    if (title.trim()) form.append('title', title.trim())
    if (includeCategory) form.append('category', category)
    if (includeReview) {
      if (draftBody.trim()) form.append('reviewed_body', draftBody)
      form.append('variable_schema', JSON.stringify(reviewedVariableSchema()))
    }
    return form
  }

  const handleAnalyze = async () => {
    if (!file) {
      setError('Choose a DOCX, PDF, or TXT sample first.')
      return
    }
    setAnalyzing(true)
    setError(null)
    try {
      const form = buildFormData()
      const result = await analyzeTemplateUpload(form)
      setAnalysis(result)
      setDraftBody(result.body || result.extracted_text || '')
      setMappedFields((result.suggested_variable_schema?.fields || []).map((field) => ({ ...field, _bodyName: field.name })))
      if (!title.trim()) setTitle(result.title || '')
    } catch (err) {
      setError(getErrorMessage(err, 'Could not analyze that sample.'))
    } finally {
      setAnalyzing(false)
    }
  }

  const handleCreate = async () => {
    if (!file || !analysis) {
      setError('Analyze the sample and review the extracted text and fields before creating the template.')
      return
    }
    const isPdfUpload = String(analysis.format || '').toLowerCase() === 'pdf'
    if (isPdfUpload && !mappedFields.some((field) => field?.pdf_field_name)) {
      setError('This PDF has no fillable form fields. Make the source PDF fillable, then upload and analyze it again.')
      return
    }
    if (!title.trim() || (!isPdfUpload && !draftBody.trim())) {
      setError(isPdfUpload ? 'Template title is required.' : 'Template title and extracted body are required.')
      return
    }
    if (mappedFields.some((field) => !normalizeVariableName(field.name))) {
      setError('Every included field needs a valid variable name.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      if (isPdfUpload) {
        // The source PDF must go through the multipart endpoint so the server
        // can retain the original bytes and its form/layout metadata.
        await createTemplateFromUpload(buildFormData({ includeCategory: true, includeReview: true }))
      } else {
        await createTemplate({
          title: title.trim(),
          body: draftBody,
          category,
          status: 'draft',
          format: analysis.format || 'markdown',
          variable_schema: {
            ...(analysis.suggested_variable_schema || {}),
            fields: reviewedFields(),
          },
          branding_profile: analysis.detected_branding_profile || {},
          description: `Draft created from reviewed upload: ${file.name}`,
        })
      }
      onCreated()
    } catch (err) {
      setError(getErrorMessage(err, 'Could not create template from upload.'))
    } finally {
      setSaving(false)
    }
  }

  const fields = mappedFields
  const branding = analysis?.detected_branding_profile || {}
  const isPdfAnalysis = String(analysis?.format || '').toLowerCase() === 'pdf'
  const hasPdfMappings = fields.some((field) => field?.pdf_field_name)

  const renameField = (index, rawName) => {
    const nextName = normalizeVariableName(rawName)
    const currentField = fields[index]
    const previousBodyName = currentField?._bodyName || currentField?.name
    setMappedFields((current) => current.map((field, fieldIndex) => (
      fieldIndex === index
        ? { ...field, name: nextName, _bodyName: nextName || field._bodyName || field.name }
        : field
    )))
    if (previousBodyName && nextName) setDraftBody((current) => replaceTemplateVariable(current, previousBodyName, nextName))
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-3 py-2">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_220px] gap-4">
        <div>
          <label htmlFor="template-sample-file" className="block text-sm font-medium text-brand-ink mb-1">
            Sample document
          </label>
          <input
            id="template-sample-file"
            type="file"
            accept=".docx,.pdf,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"
            onChange={(e) => {
              setFile(e.target.files?.[0] || null)
              setAnalysis(null)
              setDraftBody('')
              setMappedFields([])
            }}
            className="block w-full text-sm text-brand-ink file:mr-3 file:px-3 file:py-2 file:rounded file:border file:border-brand-line file:bg-brand-bg file:text-brand-ink file:text-xs file:font-semibold"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-brand-ink mb-1">
            Category
          </label>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
          >
            {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-brand-ink mb-1">
          Template title
        </label>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
          placeholder="Auto-filled from file name if blank"
        />
      </div>

      <div className="flex flex-col sm:flex-row gap-3">
        <button
          type="button"
          onClick={handleAnalyze}
          disabled={analyzing || !file}
          className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-brand-ink border border-brand-line rounded hover:bg-brand-bg disabled:opacity-50"
        >
          <Wand2 size={16} />
          {analyzing ? 'Analyzing...' : 'Analyze sample'}
        </button>
        <button
          type="button"
          onClick={handleCreate}
          disabled={saving || !file || !analysis || (isPdfAnalysis && !hasPdfMappings)}
          className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded disabled:opacity-50"
        >
          <Upload size={16} />
          {saving ? 'Creating...' : analysis ? 'Create reviewed template' : 'Analyze before creating'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink border border-brand-line rounded"
        >
          Cancel
        </button>
      </div>

      {analysis && (
        <div className="space-y-4 pt-2">
          {isPdfAnalysis && (
            <div className="border border-brand-green/30 rounded bg-brand-green/10 p-4 text-sm text-brand-ink">
              <p className="font-semibold">Original PDF design preserved</p>
              <p className="mt-1 text-brand-muted">The source PDF remains the rendering canvas. Field mappings fill its PDF form controls; extracted text is used only for search and smart-fill context.</p>
            </div>
          )}
          {isPdfAnalysis && !hasPdfMappings && (
            <div role="alert" className="border border-brand-amber/40 rounded bg-brand-amber/10 p-4 text-sm text-brand-ink">
              <p className="font-semibold">No fillable PDF form fields detected</p>
              <p className="mt-1 text-brand-muted">This sample cannot be created as a generation template. Make the source PDF fillable with AcroForm fields, then upload and analyze it again.</p>
            </div>
          )}
          {String(analysis.format || '').toLowerCase() === 'docx' && (
            <div role="alert" className="border border-brand-amber/40 rounded bg-brand-amber/10 p-4 text-sm text-brand-ink">
              <p className="font-semibold">Text-extraction template</p>
              <p className="mt-1 text-brand-muted">DOCX samples are currently imported as reviewed text. Review the extracted Markdown below before creating this draft.</p>
            </div>
          )}
          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px] gap-4">
          <div className="border border-brand-line rounded bg-brand-bg p-4">
            <div className="flex items-center justify-between gap-3 mb-3">
              <div>
                <p className="text-sm font-semibold text-brand-ink">{analysis.title}</p>
                <p className="text-xs text-brand-muted uppercase">{analysis.format}</p>
              </div>
              <span className="text-xs text-brand-muted">{fields.length} field{fields.length === 1 ? '' : 's'}</span>
            </div>
            <label htmlFor="reviewed-template-body" className="block text-xs font-semibold text-brand-muted mb-2">{isPdfAnalysis ? 'Smart-fill/search text (does not alter page design)' : 'Extracted template body'}</label>
            <textarea id="reviewed-template-body" value={draftBody} readOnly={isPdfAnalysis} onChange={(event) => setDraftBody(event.target.value)} rows={18} className="w-full rounded border border-brand-line bg-brand-surface-2 p-3 font-mono text-xs text-brand-ink read-only:opacity-75" />
          </div>

          <div className="space-y-3">
            <div className="border border-brand-line rounded bg-brand-bg p-4">
              <p className="text-sm font-semibold text-brand-ink mb-2">Detected fields</p>
              {fields.length > 0 ? (
                <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
                  {fields.map((field, index) => (
                    <div key={`${index}-${field.name}`} className="text-xs">
                      <label htmlFor={`mapped-field-${index}`} className="block text-brand-muted mb-1">{field.label || `Field ${index + 1}`}</label>
                      <input id={`mapped-field-${index}`} value={field.name || ''} onChange={(event) => renameField(index, event.target.value)} className="w-full rounded border border-brand-line bg-brand-surface-2 px-2 py-1.5 font-mono text-brand-ink" />
                      {field.pdf_field_name && <p className="mt-1 font-mono text-brand-accent-2">PDF field: {field.pdf_field_name}</p>}
                      {(field.example || field.source_path) && <p className="mt-1 text-brand-muted break-words">{field.example || field.source_path}</p>}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-brand-muted">No fields detected yet.</p>
              )}
            </div>

            <div className="border border-brand-line rounded bg-brand-bg p-4">
              <p className="text-sm font-semibold text-brand-ink mb-2">Letterhead</p>
              <p className="text-xs text-brand-muted whitespace-pre-wrap">
                {branding.letterhead_detected
                  ? branding.header_text || 'Letterhead-like header detected.'
                  : 'No letterhead detected.'}
              </p>
            </div>

            {analysis.warnings?.length > 0 && (
              <div className="border border-brand-amber/40 rounded bg-brand-amber/10 p-4">
                <p className="text-sm font-semibold text-brand-ink mb-2">Review notes</p>
                <ul className="space-y-1">
                  {analysis.warnings.map((warning) => (
                    <li key={warning} className="text-xs text-brand-muted">{warning}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          </div>
        </div>
      )}
    </div>
  )
}

function MatterPicker({ matters, selectedMatterId, onSelect, loading }) {
  const [query, setQuery] = useState('')
  const selected = matters.find((matter) => matter.id === selectedMatterId)
  const filtered = matters.filter((matter) => {
    const q = query.trim().toLowerCase()
    if (!q) return true
    return (
      matter.matter_name?.toLowerCase().includes(q) ||
      matter.client_name?.toLowerCase().includes(q) ||
      matter.practice_area?.toLowerCase().includes(q) ||
      matter.id?.toLowerCase().includes(q)
    )
  }).slice(0, 8)

  return (
    <div className="border border-brand-line rounded bg-brand-bg p-3">
      <label className="block text-sm font-medium text-brand-ink mb-2">
        Matter
      </label>
      <div className="relative">
        <Search size={15} className="absolute left-3 top-2.5 text-brand-muted" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-full pl-9 pr-3 py-2 border border-brand-line rounded text-sm bg-brand-surface-2 text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
          placeholder={loading ? 'Loading matters...' : 'Search by matter, client, or practice area'}
        />
      </div>
      {selected && (
        <div className="mt-2 flex items-center justify-between gap-2 text-xs bg-brand-surface-2 border border-brand-line rounded px-3 py-2">
          <span className="text-brand-ink truncate">{formatMatterLabel(selected)}</span>
          <button
            type="button"
            onClick={() => onSelect('')}
            className="text-brand-muted hover:text-brand-ink"
          >
            Clear
          </button>
        </div>
      )}
      <div className="mt-2 max-h-48 overflow-y-auto space-y-1">
        {filtered.map((matter) => (
          <button
            key={matter.id}
            type="button"
            onClick={() => onSelect(matter.id)}
            className={`w-full text-left px-3 py-2 rounded border text-sm transition-colors ${
              selectedMatterId === matter.id
                ? 'border-brand-accent bg-brand-accent/10 text-brand-ink'
                : 'border-transparent hover:border-brand-line hover:bg-brand-surface-2 text-brand-ink'
            }`}
          >
            <span className="block font-medium truncate">{matter.matter_name || 'Untitled matter'}</span>
            <span className="block text-xs text-brand-muted truncate">
              {[matter.client_name, matter.practice_area, matter.status].filter(Boolean).join(' - ') || matter.id}
            </span>
          </button>
        ))}
        {!loading && filtered.length === 0 && (
          <p className="text-xs text-brand-muted px-1 py-2">
            No matching matters. Paste a matter UUID below if needed.
          </p>
        )}
      </div>
    </div>
  )
}

function RenderModal({ template, matters, matterLoading, onClose }) {
  const [variables, setVariables] = useState({})
  const [matterId, setMatterId] = useState('')
  const [rendered, setRendered] = useState(null)
  const [matterDocId, setMatterDocId] = useState(null)
  const [savedDownloadUrl, setSavedDownloadUrl] = useState('')
  const [outputFilename, setOutputFilename] = useState('')
  const [outputFormat, setOutputFormat] = useState('')
  const [storageBackend, setStorageBackend] = useState('')
  const [storageWarning, setStorageWarning] = useState('')
  const [filePreview, setFilePreview] = useState(null)
  const [filePreviewUrl, setFilePreviewUrl] = useState('')
  const [rendering, setRendering] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)
  const [smartFillState, setSmartFillState] = useState('idle')
  const [smartFillMessage, setSmartFillMessage] = useState('')

  const names = useMemo(() => getTemplateVariables(template), [template])
  const fieldDefinitions = useMemo(() => Object.fromEntries(
    (template?.variable_schema?.fields || [])
      .filter((field) => field?.name)
      .map((field) => [field.name, field]),
  ), [template])
  const isPdfTemplate = String(template?.format || '').toLowerCase() === 'pdf'
  const canSaveToMatter = Boolean(template?.is_active)
  const fillableNames = useMemo(
    () => names.filter((name) => fieldDefinitions[name]?.field_type !== 'signature'),
    [names, fieldDefinitions],
  )
  const requiredUnresolvedNames = fillableNames.filter((name) => {
    const field = fieldDefinitions[name]
    if (!field?.required) return false
    if (field.field_type === 'checkbox') return variables[name] !== 'true'
    return !String(variables[name] || '').trim()
  })
  const optionalUnfilledNames = fillableNames.filter((name) => {
    const field = fieldDefinitions[name]
    if (field?.required) return false
    if (field?.field_type === 'checkbox') return variables[name] === ''
    return !String(variables[name] || '').trim()
  })

  useEffect(() => {
    const initialVars = {}
    fillableNames.forEach((name) => {
      initialVars[name] = fieldDefinitions[name]?.field_type === 'checkbox' ? 'false' : ''
    })
    setVariables(initialVars)
    setSaved(false)
    setRendered(null)
    setMatterDocId(null)
    setSavedDownloadUrl('')
    setOutputFilename('')
    setOutputFormat('')
    setStorageBackend('')
    setStorageWarning('')
    setFilePreview(null)
    setFilePreviewUrl('')
  }, [fillableNames, fieldDefinitions])

  useEffect(() => () => {
    if (filePreviewUrl) URL.revokeObjectURL(filePreviewUrl)
  }, [filePreviewUrl])

  const invalidatePreview = () => {
    setRendered(null)
    setFilePreview(null)
    setFilePreviewUrl('')
    setOutputFilename('')
    setOutputFormat('')
  }

  const setVariable = (name, value) => {
    setSaved(false)
    invalidatePreview()
    setVariables((prev) => ({ ...prev, [name]: value }))
  }

  const normalizeDiscovery = (res) => {
    const values = res?.variables || res?.values || res?.field_values || {}
    const next = {}
    if (Array.isArray(values)) {
      values.forEach((item) => {
        const key = item?.variable || item?.name || item?.key
        if (!key) return
        next[key] = item?.suggested_value ?? item?.value ?? item?.text ?? ''
      })
      return next
    }
    Object.entries(values).forEach(([key, value]) => {
      if (value && typeof value === 'object') {
        next[key] = value.suggested_value ?? value.value ?? value.text ?? ''
      } else {
        next[key] = value ?? ''
      }
    })
    return next
  }

  const handleSmartFill = async () => {
    if (!matterId.trim()) {
      setSmartFillState('error')
      setSmartFillMessage('Choose a matter before smart fill.')
      return
    }
    setSmartFillState('loading')
    setSmartFillMessage('')
    try {
      const res = await discoverTemplateVariables(template.id, {
        matter_id: matterId.trim(),
        variables: fillableNames,
      })
      const discovered = normalizeDiscovery(res)
      if (Object.keys(discovered).length === 0) {
        setSmartFillState('empty')
        setSmartFillMessage('No smart-fill values were returned for this template yet.')
        return
      }
      setVariables((prev) => ({ ...prev, ...discovered }))
      invalidatePreview()
      setSaved(false)
      setSmartFillState('ready')
      setSmartFillMessage('Smart-fill values loaded. Review each field before saving.')
    } catch (err) {
      if ([404, 405, 501].includes(err?.response?.status)) {
        setSmartFillState('unavailable')
        setSmartFillMessage('Smart fill is not enabled on this server yet. Manual fields are ready for review.')
      } else {
        setSmartFillState('error')
        setSmartFillMessage(getErrorMessage(err, 'Smart fill failed.'))
      }
    }
  }

  const handleRender = async () => {
    setRendering(true)
    setError(null)
    try {
      const payload = { variables, matter_id: null }
      if (isPdfTemplate) {
        const result = await renderTemplateFile(template.id, payload)
        const nextUrl = URL.createObjectURL(result.blob)
        setFilePreview({ blob: result.blob, filename: result.filename, contentType: result.contentType })
        setFilePreviewUrl(nextUrl)
        setOutputFilename(result.filename)
        setOutputFormat('pdf')
        setRendered(null)
      } else {
        const res = await renderTemplate(template.id, payload)
        setRendered(res.rendered)
        setFilePreview(null)
        setFilePreviewUrl('')
      }
      setMatterDocId(null)
      setSaved(false)
    } catch (err) {
      setError(getErrorMessage(err, 'Render failed.'))
    } finally {
      setRendering(false)
    }
  }

  const handleSave = async () => {
    if (!matterId.trim()) return
    if (!canSaveToMatter) {
      setError('Activate this template after verifying its preview before saving a generated document to a matter.')
      return
    }
    if (requiredUnresolvedNames.length > 0) {
      setError(`Complete ${requiredUnresolvedNames.length} required field${requiredUnresolvedNames.length === 1 ? '' : 's'} before saving.`)
      return
    }
    setSaving(true)
    setError(null)
    setStorageWarning('')
    try {
      const res = await renderTemplate(template.id, {
        variables,
        matter_id: matterId.trim(),
      })
      if (!isPdfTemplate) setRendered(res.rendered || rendered)
      setSavedDownloadUrl(res.download_url || '')
      setOutputFilename(res.output_filename || res.filename || outputFilename || '')
      setOutputFormat(res.output_format || res.format || (isPdfTemplate ? 'pdf' : 'markdown'))
      setStorageBackend(res.storage_backend || '')
      setStorageWarning(res.storage_warning || '')
      if (res.matter_document_id) {
        setMatterDocId(res.matter_document_id)
        setSaved(true)
      } else {
        setError('The server rendered the text but did not return a saved matter document.')
      }
    } catch (err) {
      setError(getErrorMessage(err, 'Save failed.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title={`${canSaveToMatter ? (isPdfTemplate ? 'Generate PDF' : 'Generate Document') : 'Preview Draft'}: ${template.title}`} onClose={onClose}>
      <div className="space-y-4">
        {error && (
          <div className="text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-3 py-2">
            {error}
          </div>
        )}

        {!canSaveToMatter && (
          <div role="status" className="text-sm text-brand-amber bg-brand-amber/10 border border-brand-amber/30 px-3 py-2">
            This template is inactive. Preview and verify it here, then activate it before saving any generated document to a matter.
          </div>
        )}

        <MatterPicker
          matters={matters}
          selectedMatterId={matterId}
          onSelect={(id) => { setMatterId(id); setSaved(false) }}
          loading={matterLoading}
        />

        <div>
          <label className="block text-xs font-medium text-brand-muted mb-0.5">
            Matter UUID fallback
          </label>
          <input
            type="text"
            value={matterId}
            onChange={(e) => { setMatterId(e.target.value); setSaved(false) }}
            className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent font-mono"
            placeholder="Paste matter UUID if the matter is not listed"
          />
        </div>

        {names.length > 0 && (
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 border border-brand-line rounded bg-brand-bg px-3 py-2">
            <div>
              <p className="text-sm font-medium text-brand-ink">Smart fill</p>
              <p className="text-xs text-brand-muted">
                Pull matter-aware values when the backend endpoint is available.
              </p>
            </div>
            <button
              onClick={handleSmartFill}
              disabled={smartFillState === 'loading' || !matterId.trim()}
              className="flex items-center justify-center gap-2 px-3 py-2 text-sm text-brand-ink border border-brand-line rounded hover:bg-brand-surface-2 disabled:opacity-50"
            >
              <Wand2 size={15} />
              {smartFillState === 'loading' ? 'Filling...' : 'Smart Fill'}
            </button>
          </div>
        )}

        {smartFillMessage && (
          <div className={`text-sm border px-3 py-2 ${
            smartFillState === 'ready'
              ? 'text-brand-green bg-brand-green/10 border-brand-green/30'
              : smartFillState === 'error'
                ? 'text-brand-rose bg-brand-rose/10 border-brand-rose/30'
                : 'text-brand-muted bg-brand-bg border-brand-line'
          }`}>
            {smartFillMessage}
          </div>
        )}

        {names.length > 0 && (
          <div>
            <h3 className="text-sm font-medium text-brand-ink mb-2">
              Fields To Review
            </h3>
            <div className="space-y-2">
              {names.map((name) => {
                const field = fieldDefinitions[name] || {}
                const fieldType = field.field_type || 'text'
                const label = field.label || friendlyVariableLabel(name)
                const inputId = `template-variable-${name}`
                const options = (field.options || []).map((option) => (
                  typeof option === 'object'
                    ? { value: option.value ?? option.name ?? option.label ?? '', label: option.label ?? option.name ?? option.value ?? '' }
                    : { value: option, label: option }
                ))
                return (
                <div key={name} className={fieldType === 'signature' ? 'border border-brand-line rounded bg-brand-bg px-3 py-2' : ''}>
                  <label htmlFor={fieldType === 'signature' ? undefined : inputId} className="block text-xs font-medium text-brand-muted mb-0.5">
                    {label}{field.required && fieldType !== 'signature' ? ' *' : ''}
                    <span className="font-mono text-brand-muted ml-2">
                      {'{{'}{name}{'}}'}
                    </span>
                  </label>
                  {fieldType === 'signature' ? (
                    <p className="text-sm text-brand-muted">
                      Signature area is left blank for signing; it is not populated during document generation.
                      {field.pdf_field_name ? ` PDF field: ${field.pdf_field_name}.` : ''}
                    </p>
                  ) : fieldType === 'checkbox' ? (
                    <label className="inline-flex items-center gap-2 text-sm text-brand-ink py-1">
                      <input
                        id={inputId}
                        type="checkbox"
                        checked={variables[name] === 'true'}
                        onChange={(e) => setVariable(name, e.target.checked ? 'true' : 'false')}
                        className="h-4 w-4 rounded border-brand-line text-brand-accent focus:ring-brand-accent"
                      />
                      Checked
                    </label>
                  ) : (fieldType === 'choice' || fieldType === 'radio') && options.length > 0 ? (
                    <select
                      id={inputId}
                      value={variables[name] || ''}
                      onChange={(e) => setVariable(name, e.target.value)}
                      className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
                    >
                      <option value="">Select {label}</option>
                      {options.map((option) => <option key={String(option.value)} value={option.value}>{option.label}</option>)}
                    </select>
                  ) : fieldType === 'multiline' || field.multiline ? (
                    <textarea
                      id={inputId}
                      rows={3}
                      value={variables[name] || ''}
                      onChange={(e) => setVariable(name, e.target.value)}
                      className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
                      placeholder={`Enter ${label}`}
                    />
                  ) : (
                    <input
                      id={inputId}
                      type="text"
                      value={variables[name] || ''}
                      onChange={(e) => setVariable(name, e.target.value)}
                      className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-brand-bg text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
                      placeholder={`Enter ${label}`}
                    />
                  )}
                  {field.pdf_field_name && fieldType !== 'signature' && (
                    <p className="mt-1 text-[11px] text-brand-muted">PDF field: {field.pdf_field_name}{field.page ? ` · Page ${field.page}` : ''}</p>
                  )}
                </div>
                )
              })}
            </div>
            <p className={`mt-2 text-xs ${requiredUnresolvedNames.length ? 'text-brand-amber' : 'text-brand-green'}`} role="status">
              {requiredUnresolvedNames.length
                ? `${requiredUnresolvedNames.length} required field${requiredUnresolvedNames.length === 1 ? '' : 's'} still need review before saving.`
                : 'All required fields are ready.'}
            </p>
            {optionalUnfilledNames.length > 0 && (
              <p className="mt-1 text-xs text-brand-muted">
                {optionalUnfilledNames.length} optional field{optionalUnfilledNames.length === 1 ? '' : 's'} left unfilled; saving is still allowed.
              </p>
            )}
          </div>
        )}

        {names.length === 0 && (
          <p className="text-sm text-brand-muted italic">
            This template has no variables. Preview or save it directly.
          </p>
        )}

        <div className="flex flex-col sm:flex-row gap-3">
          <button
            onClick={handleRender}
            disabled={rendering}
            className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded disabled:opacity-50"
          >
            <Eye size={16} />
            {rendering ? 'Rendering...' : 'Preview'}
          </button>
          <button
            onClick={handleSave}
            disabled={saving || saved || !matterId.trim() || !canSaveToMatter}
            title={!canSaveToMatter ? 'Activate this verified template before saving to a matter' : undefined}
            className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-accent hover:opacity-90 rounded disabled:opacity-50"
          >
            {saved ? (
              <>
                <Check size={16} /> Saved
              </>
            ) : (
              <>
                <Send size={16} />
                {saving ? 'Saving...' : 'Render & Save to Matter'}
              </>
            )}
          </button>
        </div>

        {rendered && (
          <div>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-medium text-brand-ink">
              {outputFormat === 'pdf' ? 'PDF Preview' : 'Text Preview'}
            </h3>
              <button type="button" onClick={() => downloadRenderedText(rendered, template.title)} className="inline-flex items-center gap-1.5 rounded border border-brand-line px-3 py-1.5 text-xs font-semibold text-brand-ink hover:bg-brand-surface-2">
                <Download size={14} /> Download preview
              </button>
            </div>
            <div className="bg-brand-bg border border-brand-line rounded p-4 whitespace-pre-wrap font-mono text-sm text-brand-ink max-h-96 overflow-y-auto">
              {rendered}
            </div>
          </div>
        )}

        {filePreviewUrl && filePreview && (
          <div>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-medium text-brand-ink">PDF Preview</h3>
                <p className="text-xs text-brand-muted">{filePreview.filename}</p>
              </div>
              <button type="button" onClick={() => triggerBlobDownload(filePreview.blob, filePreview.filename)} className="inline-flex items-center gap-1.5 rounded border border-brand-line px-3 py-1.5 text-xs font-semibold text-brand-ink hover:bg-brand-surface-2">
                <Download size={14} /> Download preview
              </button>
            </div>
            <object title={`Preview of ${template.title}`} data={filePreviewUrl} type="application/pdf" className="h-[65vh] min-h-[480px] w-full rounded border border-brand-line bg-white">
              <p className="p-4 text-sm text-brand-muted">This browser cannot display the PDF inline. Use Download preview instead.</p>
            </object>
          </div>
        )}

        {matterDocId && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-brand-green/30 bg-brand-green/10 px-3 py-2">
              <p className="text-xs text-brand-green">
                Saved to the matter{outputFilename ? ` as ${outputFilename}` : ''}{outputFormat ? ` (${outputFormat.toUpperCase()})` : ''}{storageBackend ? ` in ${storageBackend.replaceAll('_', ' ')}` : ''}.
              </p>
              <a href={savedDownloadUrl || getMatterDocumentDownloadUrl(matterId.trim(), matterDocId)} className="inline-flex items-center gap-1.5 text-xs font-semibold text-brand-accent-2 underline">
                <Download size={14} /> Download saved document
              </a>
            </div>
            {storageWarning && (
              <div role="alert" className="rounded border border-brand-amber/40 bg-brand-amber/10 px-3 py-2 text-xs text-brand-ink">
                {storageWarning}
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  )
}

export default function TemplatesPage() {
  const [templates, setTemplates] = useState([])
  const [matters, setMatters] = useState([])
  const [loading, setLoading] = useState(true)
  const [matterLoading, setMatterLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('templates')
  const [showCreate, setShowCreate] = useState(false)
  const [showUpload, setShowUpload] = useState(false)
  const [editTemplate, setEditTemplate] = useState(null)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [renderTarget, setRenderTarget] = useState(null)

  const load = useCallback(async () => {
    try {
      setError(null)
      const res = await getTemplates({ include_inactive: true })
      setTemplates(res.items || [])
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to load templates.'))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadMatters = useCallback(async () => {
    setMatterLoading(true)
    try {
      const res = await getMattersV2({
        page_size: 100,
        sort_by: 'updated_at',
        sort_dir: 'desc',
      })
      setMatters(normalizeItems(res))
    } catch {
      setMatters([])
    } finally {
      setMatterLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    loadMatters()
  }, [load, loadMatters])

  const handleCreate = async (data) => {
    await createTemplate(data)
    setShowCreate(false)
    await load()
  }

  const handleUploadedTemplate = async () => {
    setShowUpload(false)
    await load()
  }

  const handleUpdate = async (data) => {
    await updateTemplate(editTemplate.id, data)
    setEditTemplate(null)
    await load()
  }

  const handleDelete = async () => {
    try {
      await deleteTemplate(deleteTarget.id)
      setDeleteTarget(null)
      await load()
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to delete template.'))
    }
  }

  const toggleActive = async (tpl) => {
    try {
      await updateTemplate(tpl.id, { is_active: !tpl.is_active })
      await load()
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to toggle template status.'))
    }
  }

  const bodyPreview = (body) => {
    const text = String(body || '')
    return text.length > 100 ? `${text.slice(0, 100)}...` : text
  }

  const activeGenerationTemplates = templates.filter((tpl) => tpl.is_active && !isPdfSourceMissing(tpl))
  const activeTemplateCount = templates.filter((tpl) => tpl.is_active).length
  const variableCount = templates.reduce((sum, tpl) => sum + getTemplateVariables(tpl).length, 0)
  const selectedTemplate = activeGenerationTemplates[0] || null

  const renderTemplatesPanel = () => {
    if (templates.length === 0) {
      return (
        <div className="text-center py-16 bg-brand-surface-2 border border-brand-line rounded-lg">
          <FileText
            size={48}
            className="mx-auto text-brand-muted mb-4 opacity-30"
          />
          <p className="text-brand-muted">
            No templates yet. Create the first automation template.
          </p>
        </div>
      )
    }

    return (
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {templates.map((tpl) => {
          const vars = getTemplateVariables(tpl)
          const sourceMissing = isPdfSourceMissing(tpl)
          return (
            <div
              key={tpl.id}
              className="bg-brand-surface-2 border border-brand-line rounded-lg shadow-sm hover:shadow-md transition-shadow"
            >
              <div className="p-4">
                <div className="flex items-start justify-between mb-2">
                  <h3 className="font-medium text-brand-ink truncate">
                    {tpl.title}
                  </h3>
                  <span
                    className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full font-semibold shrink-0 ml-2 ${
                      CATEGORY_COLORS[tpl.category] || CATEGORY_COLORS.other
                    }`}
                  >
                    {CATEGORY_LABELS[tpl.category] || tpl.category}
                  </span>
                </div>
                <p className="text-xs text-brand-muted font-mono mb-3 min-h-10">
                  {bodyPreview(tpl.body)}
                </p>

                {sourceMissing && (
                  <div role="alert" className="mb-3 border border-brand-amber/40 rounded bg-brand-amber/10 px-3 py-2">
                    <p className="text-xs font-semibold text-brand-ink">Source missing — recreate this PDF template</p>
                    <p className="mt-0.5 text-[11px] text-brand-muted">This older record cannot generate documents. Create a replacement from the original fillable PDF, verify it, then remove this record.</p>
                    <button
                      type="button"
                      onClick={() => setShowUpload(true)}
                      className="mt-2 text-xs font-semibold text-brand-accent-2 underline"
                    >
                      Recreate from Upload Sample
                    </button>
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-2 mb-3">
                  <span className="inline-flex items-center text-xs font-semibold uppercase text-brand-accent-2 border border-brand-line rounded px-2 py-1">
                    {tpl.format || 'markdown'}
                  </span>
                  <span className="inline-flex items-center gap-1 text-xs text-brand-muted border border-brand-line rounded px-2 py-1">
                    <ClipboardList size={12} />
                    {vars.length} field{vars.length === 1 ? '' : 's'}
                  </span>
                  <button
                    onClick={() => toggleActive(tpl)}
                    disabled={sourceMissing}
                    title={sourceMissing ? 'Recreate the template from its original PDF before activating it' : undefined}
                    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                      tpl.is_active ? 'bg-brand-green' : 'bg-brand-muted/30'
                    }`}
                    aria-label={tpl.is_active ? 'Deactivate template' : 'Activate template'}
                  >
                    <span
                      className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                        tpl.is_active ? 'translate-x-4' : 'translate-x-1'
                      }`}
                    />
                  </button>
                  <span className="text-xs text-brand-muted">
                    {tpl.is_active ? 'Active' : 'Inactive'}
                  </span>
                </div>

                <div className="flex items-center gap-2 pt-2 border-t border-brand-line">
                  <button
                    onClick={() => setEditTemplate(tpl)}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs text-brand-muted hover:text-brand-ink border border-brand-line rounded"
                  >
                    <Pencil size={14} />
                    Edit
                  </button>
                  <button
                    onClick={() => setRenderTarget(tpl)}
                    disabled={sourceMissing}
                    title={sourceMissing
                      ? 'Recreate the template from its original PDF before previewing it'
                      : (tpl.is_active ? 'Generate a document' : 'Preview this draft; activate it before saving to a matter')}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs text-brand-muted hover:text-brand-ink border border-brand-line rounded disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {tpl.is_active ? <Sparkles size={14} /> : <Eye size={14} />}
                    {tpl.is_active ? 'Generate' : 'Preview draft'}
                  </button>
                  <button
                    onClick={() => setDeleteTarget(tpl)}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs text-brand-rose hover:text-red-800 border border-brand-line rounded ml-auto"
                  >
                    <Trash2 size={14} />
                    Delete
                  </button>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    )
  }

  const renderGeneratePanel = () => (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_340px] gap-5">
      <div className="bg-brand-surface-2 border border-brand-line rounded-lg p-5">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-brand-ink">Generate / Smart Fill</h2>
            <p className="text-sm text-brand-muted mt-1">
              Start from an active template, choose a matter, review fields, preview, then save the generated document to the matter.
            </p>
          </div>
          <button
            onClick={() => selectedTemplate && setRenderTarget(selectedTemplate)}
            disabled={!selectedTemplate || isPdfSourceMissing(selectedTemplate)}
            title={isPdfSourceMissing(selectedTemplate) ? 'Re-upload the source PDF before generating' : undefined}
            className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded disabled:opacity-50"
          >
            <Sparkles size={16} />
            Generate
          </button>
        </div>

        <div className="mt-5 grid grid-cols-1 md:grid-cols-2 gap-3">
          {activeGenerationTemplates.slice(0, 6).map((tpl) => {
            const vars = getTemplateVariables(tpl)
            return (
              <button
                key={tpl.id}
                onClick={() => setRenderTarget(tpl)}
                className="text-left border border-brand-line rounded p-4 bg-brand-bg hover:border-brand-accent/50"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-brand-ink truncate">{tpl.title}</p>
                    <p className="text-xs text-brand-muted mt-1">
                      {vars.length} review field{vars.length === 1 ? '' : 's'}
                    </p>
                  </div>
                  <span className="text-[10px] uppercase px-2 py-0.5 rounded-full font-semibold shrink-0 bg-brand-green/10 text-brand-green">
                    Active
                  </span>
                </div>
              </button>
            )
          })}
          {activeGenerationTemplates.length === 0 && (
            <div className="border border-dashed border-brand-line rounded p-6 text-center md:col-span-2">
              <p className="text-sm text-brand-muted">Activate a verified template before generating matter documents.</p>
            </div>
          )}
        </div>
      </div>

      <div className="bg-brand-surface-2 border border-brand-line rounded-lg p-5">
        <h3 className="text-sm font-semibold text-brand-ink">Integration Hooks</h3>
        <div className="mt-4 space-y-3">
          <div className="flex gap-3">
            <Check size={16} className="text-brand-green shrink-0 mt-0.5" />
            <p className="text-sm text-brand-muted">Existing render endpoint remains the source of truth.</p>
          </div>
          <div className="flex gap-3">
            <Clock3 size={16} className="text-brand-amber shrink-0 mt-0.5" />
            <p className="text-sm text-brand-muted">Optional smart-fill discovery calls are attempted only from the generate modal.</p>
          </div>
          <div className="flex gap-3">
            <AlertCircle size={16} className="text-brand-muted shrink-0 mt-0.5" />
            <p className="text-sm text-brand-muted">If discovery is unavailable, users keep the manual review fields.</p>
          </div>
        </div>
      </div>
    </div>
  )

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full bg-brand-bg">
        <div className="text-brand-muted">Loading document automation...</div>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto bg-brand-bg p-4 md:p-6">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4 mb-5">
        <div>
          <h1 className="text-xl font-semibold text-brand-ink">
            Document Automation
          </h1>
          <p className="text-sm text-brand-muted mt-1">
            Build high-fidelity templates, smart-fill matter data, preview the result, and save finalized documents to the matter file.
          </p>
        </div>
        <div className="flex flex-col sm:flex-row gap-2">
          <button
            onClick={() => setShowUpload(true)}
            className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-brand-ink border border-brand-line rounded hover:bg-brand-surface-2"
          >
            <Upload size={18} />
            Upload Sample
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center justify-center gap-2 px-4 py-2 text-sm text-white bg-brand-ink hover:bg-brand-ink-2 rounded"
          >
            <Plus size={18} />
            New Template
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
        <div className="bg-brand-surface-2 border border-brand-line rounded p-4">
          <p className="text-xs text-brand-muted uppercase tracking-wider">Templates</p>
          <p className="text-2xl font-semibold text-brand-ink mt-1">{templates.length}</p>
        </div>
        <div className="bg-brand-surface-2 border border-brand-line rounded p-4">
          <p className="text-xs text-brand-muted uppercase tracking-wider">Active</p>
          <p className="text-2xl font-semibold text-brand-ink mt-1">{activeTemplateCount}</p>
        </div>
        <div className="bg-brand-surface-2 border border-brand-line rounded p-4">
          <p className="text-xs text-brand-muted uppercase tracking-wider">Mapped Fields</p>
          <p className="text-2xl font-semibold text-brand-ink mt-1">{variableCount}</p>
        </div>
        <div className="bg-brand-surface-2 border border-brand-line rounded p-4">
          <p className="text-xs text-brand-muted uppercase tracking-wider">Recent Matters</p>
          <p className="text-2xl font-semibold text-brand-ink mt-1">{matters.length}</p>
        </div>
      </div>

      <div className="mb-5 overflow-x-auto border-b border-brand-line">
        <div className="flex min-w-max gap-1">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`flex items-center gap-2 px-4 py-3 text-sm border-b-2 transition-colors ${
                activeTab === key
                  ? 'border-brand-accent text-brand-ink'
                  : 'border-transparent text-brand-muted hover:text-brand-ink'
              }`}
            >
              <Icon size={16} />
              {label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="mb-4 text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-4 py-3">
          {error}
        </div>
      )}

      {activeTab === 'templates' && renderTemplatesPanel()}
      {activeTab === 'generate' && renderGeneratePanel()}

      {showCreate && (
        <Modal title="Create Template" onClose={() => setShowCreate(false)}>
          <TemplateForm
            onSubmit={handleCreate}
            onCancel={() => setShowCreate(false)}
          />
        </Modal>
      )}

      {showUpload && (
        <Modal title="Create Template From Sample" onClose={() => setShowUpload(false)}>
          <UploadTemplateForm
            onCreated={handleUploadedTemplate}
            onCancel={() => setShowUpload(false)}
          />
        </Modal>
      )}

      {editTemplate && (
        <Modal title="Edit Template" onClose={() => setEditTemplate(null)}>
          <TemplateForm
            initial={editTemplate}
            onSubmit={handleUpdate}
            onCancel={() => setEditTemplate(null)}
          />
        </Modal>
      )}

      {deleteTarget && (
        <ConfirmDialog
          message={`Delete "${deleteTarget.title}"? This cannot be undone.`}
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}

      {renderTarget && (
        <RenderModal
          template={renderTarget}
          matters={matters}
          matterLoading={matterLoading}
          onClose={() => setRenderTarget(null)}
        />
      )}
    </div>
  )
}
